"use client";
import * as React from "react";
import { Suspense, use } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Compass, MoreHorizontal, Pencil, RefreshCw, TerminalSquare, Trash2, Activity } from "lucide-react";
import { datasets } from "@/lib/api/resources/data";
import { ApiError } from "@/lib/api/client";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader, Panel } from "@/components/shell/page";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { StatusBadge } from "@/components/analysis/status";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states/states";
import { ProfileView } from "@/components/data/profile-view";
import {
  FreshnessTab,
  LineageTab,
  MetricsTab,
  PreviewTab,
  QualityTab,
  RelationshipsTab,
  SchemaTab,
} from "@/components/data/dataset-tabs";
import { JobProgress, useJobRunner } from "@/components/data/use-job";
import { formatInt, formatRelative, humanize } from "@/lib/format";

const TABS = ["schema", "profile", "relationships", "quality", "preview", "freshness", "lineage", "metrics"] as const;
type Tab = (typeof TABS)[number];

function DatasetInner({ id }: { id: string }) {
  const { id: ws, href, canEdit } = useWorkspace();
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const tabParam = params.get("tab") as Tab | null;
  const tab: Tab = tabParam && (TABS as readonly string[]).includes(tabParam) ? tabParam : "schema";
  const column = params.get("column");
  const [editing, setEditing] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const { run, progress } = useJobRunner();

  const ds = useQuery({ queryKey: qk.dataset(ws, id), queryFn: () => datasets.get(ws, id) });
  const profile = useQuery({
    queryKey: ["ws", ws, "datasets", id, "profile"],
    queryFn: () => datasets.profile(ws, id),
    enabled: !!ds.data && ds.data.profile_status !== "pending" && ds.data.profile_status !== "running",
    retry: false,
  });
  const profiling = ds.data?.profile_status === "running" || ds.data?.profile_status === "pending";

  React.useEffect(() => {
    if (!profiling) return;
    const t = setInterval(() => void qc.invalidateQueries({ queryKey: qk.dataset(ws, id) }), 2500);
    return () => clearInterval(t);
  }, [profiling, qc, ws, id]);

  const setTab = (t: string) => {
    const usp = new URLSearchParams(params.toString());
    usp.set("tab", t);
    if (t !== "schema" && t !== "profile") usp.delete("column");
    router.replace(`${href(`/data/${id}`)}?${usp.toString()}`, { scroll: false });
  };

  const reprofile = useMutation({
    mutationFn: () => run(() => datasets.reprofile(ws, id)),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["ws", ws, "datasets", id] });
      void qc.invalidateQueries({ queryKey: qk.datasets(ws) });
      toast.success("Profile refreshed");
    },
    onError: (e: Error) => toast.error("Profiling failed", { description: e.message }),
  });
  const update = useMutation({
    mutationFn: () => datasets.update(ws, id, { name: name.trim(), description }),
    onSuccess: () => {
      setEditing(false);
      void qc.invalidateQueries({ queryKey: qk.datasets(ws) });
    },
  });
  const remove = useMutation({
    mutationFn: () => datasets.remove(ws, id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.datasets(ws) });
      toast.success("Dataset deleted");
      router.push(href("/data"));
    },
    onError: (e: Error) => toast.error("Delete failed", { description: e.message }),
  });

  if (ds.isLoading) return <LoadingState variant="block" label="Loading dataset" />;
  if (ds.error || !ds.data)
    return (
      <ErrorState
        error={ds.error ?? new Error("Dataset not found")}
        onRetry={() => void ds.refetch()}
        className="h-full"
      />
    );
  const d = ds.data;
  const profileMissing = profile.error instanceof ApiError && profile.error.status === 404;

  return (
    <Page>
      <PageHeader
        breadcrumb={
          <>
            <Link href={href("/data")} className="hover:text-fg">
              Data
            </Link>
            <span>/</span>
            <span className="font-mono">{d.table_name}</span>
          </>
        }
        title={d.name}
        description={d.description || undefined}
        meta={
          <>
            <span className="tabular">{formatInt(d.row_count)} rows</span>
            <span>{d.columns.length} columns</span>
            <Badge tone="outline">{humanize(d.source_kind)}</Badge>
            {typeof d.source_ref?.filename === "string" ? <span className="font-mono">{d.source_ref.filename}</span> : null}
            <span className="flex items-center gap-1">
              Profile <StatusBadge status={d.profile_status} />
            </span>
            {d.issue_count ? <span className="text-warning">{d.issue_count} profile issues</span> : null}
            <span>Updated {formatRelative(d.updated_at ?? d.created_at)}</span>
          </>
        }
        actions={
          <>
            <Button asChild>
              <Link href={href(`/sql?table=${encodeURIComponent(d.table_name)}`)}>
                <TerminalSquare /> Query
              </Link>
            </Button>
            <Button asChild>
              <Link href={href(`/explore?table=${encodeURIComponent(d.table_name)}`)}>
                <Compass /> Explore
              </Link>
            </Button>
            {canEdit ? (
              <>
                <Button onClick={() => reprofile.mutate()} disabled={reprofile.isPending || profiling}>
                  {reprofile.isPending || profiling ? <Spinner /> : <RefreshCw />} Re-profile
                </Button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="icon" aria-label="More dataset actions">
                      <MoreHorizontal />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem
                      onSelect={() => {
                        setName(d.name);
                        setDescription(d.description ?? "");
                        setEditing(true);
                      }}
                    >
                      <Pencil /> Rename / describe
                    </DropdownMenuItem>
                    <DropdownMenuItem destructive onSelect={() => setDeleting(true)}>
                      <Trash2 /> Delete dataset
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </>
            ) : null}
          </>
        }
      />
      <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
        <TabsList className="px-4 sm:px-5" aria-label="Dataset sections">
          <TabsTrigger value="schema">Schema</TabsTrigger>
          <TabsTrigger value="profile">
            Profile
            {d.issue_count ? <Badge tone="warning">{d.issue_count}</Badge> : null}
          </TabsTrigger>
          <TabsTrigger value="relationships">Relationships</TabsTrigger>
          <TabsTrigger value="quality">Quality</TabsTrigger>
          <TabsTrigger value="preview">Preview</TabsTrigger>
          <TabsTrigger value="freshness">Freshness</TabsTrigger>
          <TabsTrigger value="lineage">Lineage</TabsTrigger>
          <TabsTrigger value="metrics">Metrics</TabsTrigger>
        </TabsList>
        <PageBody>
          <JobProgress progress={progress} />
          <TabsContent value="schema">
            <SchemaTab dataset={d} profile={profile.data} highlight={column} />
          </TabsContent>
          <TabsContent value="profile">
            {profiling ? (
              <LoadingState variant="block" label="Profiling in progress" />
            ) : profile.isLoading ? (
              <LoadingState rows={8} />
            ) : profileMissing ? (
              <Panel>
                <EmptyState
                  icon={Activity}
                  title={d.profile_status === "failed" ? "Profiling failed" : "No profile yet"}
                  description="Profiling computes per-column statistics and flags duplicates, mixed types, malformed dates, outliers and impossible values."
                  action={canEdit ? <Button variant="primary" onClick={() => reprofile.mutate()} disabled={reprofile.isPending}><RefreshCw /> Profile now</Button> : undefined}
                />
              </Panel>
            ) : profile.error ? (
              <ErrorState error={profile.error} onRetry={() => void profile.refetch()} />
            ) : profile.data ? (
              <ProfileView key={column ?? ""} profile={profile.data} initialColumn={column} />
            ) : null}
          </TabsContent>
          <TabsContent value="relationships">
            <RelationshipsTab dataset={d} />
          </TabsContent>
          <TabsContent value="quality">
            <QualityTab dataset={d} />
          </TabsContent>
          <TabsContent value="preview">
            <PreviewTab dataset={d} />
          </TabsContent>
          <TabsContent value="freshness">
            <FreshnessTab dataset={d} />
          </TabsContent>
          <TabsContent value="lineage">
            <LineageTab dataset={d} />
          </TabsContent>
          <TabsContent value="metrics">
            <MetricsTab dataset={d} />
          </TabsContent>
        </PageBody>
      </Tabs>

      <Dialog open={editing} onOpenChange={setEditing}>
        <DialogContent size="sm">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) update.mutate();
            }}
          >
            <DialogHeader>
              <DialogTitle>Edit dataset</DialogTitle>
            </DialogHeader>
            <DialogBody className="flex flex-col gap-3">
              <Field label="Name" htmlFor="ds-name">
                <Input id="ds-name" value={name} onChange={(e) => setName(e.target.value)} required />
              </Field>
              <Field label="Description" htmlFor="ds-desc">
                <Textarea id="ds-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
              </Field>
              <p className="text-xs text-fg-subtle">The table name ({d.table_name}) stays the same so queries keep working.</p>
              <InlineError error={update.error} />
            </DialogBody>
            <DialogFooter>
              <Button variant="secondary" onClick={() => setEditing(false)}>
                Cancel
              </Button>
              <Button variant="primary" type="submit" disabled={update.isPending || !name.trim()}>
                Save
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete ${d.name}?`}
        description={`This drops the table ${d.table_name} from the workspace store. Metrics, saved queries and investigations that use it will stop working. The original uploaded file is not affected.`}
        confirmLabel="Delete dataset"
        destructive
        pending={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
    </Page>
  );
}

export default function DatasetPage({ params }: { params: Promise<{ ws: string; id: string }> }) {
  const { id } = use(params);
  return (
    <Suspense fallback={<LoadingState variant="block" />}>
      <DatasetInner id={id} />
    </Suspense>
  );
}
