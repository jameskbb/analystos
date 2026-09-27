"use client";
import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Database, FileUp, GitMerge, MoreHorizontal, PlugZap, ShieldCheck, Trash2, FolderSearch } from "lucide-react";
import { dataSources, datasets, quality } from "@/lib/api/resources/data";
import { qk } from "@/lib/api/keys";
import type { DataSource, Dataset } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader, Panel } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/analysis/status";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/states";
import { LoadDemoButton } from "@/components/shell/use-load-demo";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { UploadWizard } from "@/components/data/upload-wizard";
import { ConnectDatabaseDialog, SourceBrowserDialog } from "@/components/data/connect-database";
import { summarizeRules } from "@/components/data/dq-summary";
import { DqBadge } from "@/components/data/dq-badge";
import { formatDateTime, formatInt, formatRelative, humanize } from "@/lib/format";

const SOURCE_LABEL: Record<string, string> = { upload: "File upload", connector: "Database snapshot", demo: "Demo" };

function DatasetsTable({ items, dq }: { items: Dataset[]; dq: ReturnType<typeof summarizeRules> | null }) {
  const { href } = useWorkspace();
  const [q, setQ] = React.useState("");
  const filtered = items.filter(
    (d) => !q || d.name.toLowerCase().includes(q.toLowerCase()) || d.table_name.toLowerCase().includes(q.toLowerCase()),
  );
  return (
    <Panel
      title={`Datasets (${items.length})`}
      actions={
        <Input
          aria-label="Filter datasets"
          placeholder="Filter…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="h-6 w-44 text-xs"
        />
      }
    >
      <div className="overflow-x-auto scrollbar-thin">
        <table className="w-full min-w-[820px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-fg-subtle">
              <th className="px-3 py-1.5 font-medium">Dataset</th>
              <th className="px-3 py-1.5 font-medium">Table</th>
              <th className="px-3 py-1.5 text-right font-medium">Rows</th>
              <th className="px-3 py-1.5 text-right font-medium">Cols</th>
              <th className="px-3 py-1.5 font-medium">Source</th>
              <th className="px-3 py-1.5 font-medium">Profile</th>
              <th className="px-3 py-1.5 font-medium">Quality</th>
              <th className="px-3 py-1.5 font-medium">Updated</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((d) => (
              <tr key={d.id} className="border-b border-border last:border-b-0 hover:bg-bg-subtle">
                <td className="px-3 py-1.5">
                  <Link href={href(`/data/${d.id}`)} className="font-medium hover:text-accent hover:underline">
                    {d.name}
                  </Link>
                  {d.description ? <div className="max-w-xs truncate text-xs text-fg-subtle">{d.description}</div> : null}
                </td>
                <td className="px-3 py-1.5 font-mono text-xs">{d.table_name}</td>
                <td className="px-3 py-1.5 text-right tabular">{formatInt(d.row_count)}</td>
                <td className="px-3 py-1.5 text-right tabular">{d.columns.length}</td>
                <td className="px-3 py-1.5">
                  <Badge tone="outline">{SOURCE_LABEL[d.source_kind] ?? humanize(d.source_kind)}</Badge>
                </td>
                <td className="px-3 py-1.5">
                  <span className="flex items-center gap-1.5">
                    <StatusBadge status={d.profile_status} />
                    {d.issue_count ? (
                      <Link href={href(`/data/${d.id}?tab=profile`)} className="text-xs text-warning hover:underline">
                        {d.issue_count} issue{d.issue_count === 1 ? "" : "s"}
                      </Link>
                    ) : null}
                  </span>
                </td>
                <td className="px-3 py-1.5">
                  {dq ? <DqBadge summary={dq.get(d.table_name)} /> : <span className="text-xs text-fg-faint">n/a</span>}
                </td>
                <td className="px-3 py-1.5 text-xs text-fg-subtle" title={formatDateTime(d.updated_at ?? d.created_at)}>
                  {formatRelative(d.updated_at ?? d.created_at)}
                </td>
              </tr>
            ))}
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-sm text-fg-subtle">
                  No datasets match “{q}”.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function SourcesPanel({ onConnect }: { onConnect: () => void }) {
  const { id: ws, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const sources = useQuery({ queryKey: qk.dataSources(ws), queryFn: () => dataSources.list(ws) });
  const [browse, setBrowse] = React.useState<DataSource | null>(null);
  const [del, setDel] = React.useState<DataSource | null>(null);
  const remove = useMutation({
    mutationFn: (id: string) => dataSources.remove(ws, id),
    onSuccess: () => {
      setDel(null);
      void qc.invalidateQueries({ queryKey: qk.dataSources(ws) });
      toast.success("Connection removed");
    },
    onError: (e: Error) => toast.error("Could not remove the connection", { description: e.message }),
  });
  return (
    <Panel
      title="Database connections"
      description="Read-only connections; tables are snapshotted into the workspace store for analysis."
      actions={
        canEdit ? (
          <Button size="xs" onClick={onConnect}>
            <PlugZap /> Connect
          </Button>
        ) : null
      }
    >
      {sources.isLoading ? (
        <LoadingState rows={2} className="px-3" />
      ) : sources.error ? (
        <ErrorState error={sources.error} compact onRetry={() => void sources.refetch()} />
      ) : !sources.data?.length ? (
        <EmptyState
          compact
          icon={PlugZap}
          title="No database connections"
          description="Connect Postgres, MySQL, SQL Server, Snowflake or BigQuery to browse and snapshot tables."
          action={canEdit ? <Button size="xs" onClick={onConnect}>Connect a database</Button> : undefined}
        />
      ) : (
        <ul>
          {sources.data.map((s) => (
            <li key={s.id} className="flex items-center gap-3 border-b border-border px-3 py-2 last:border-b-0">
              <Database className="size-4 shrink-0 text-fg-subtle" aria-hidden />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{s.name}</div>
                <div className="truncate text-xs text-fg-subtle">
                  {humanize(s.kind)}
                  {s.last_tested_at ? ` · tested ${formatRelative(s.last_tested_at)}` : " · never tested"}
                  {s.last_test_message ? ` · ${s.last_test_message}` : ""}
                </div>
              </div>
              <StatusBadge status={s.status} />
              <Button size="xs" onClick={() => setBrowse(s)}>
                <FolderSearch /> Browse
              </Button>
              {canEdit ? (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="icon-xs" aria-label={`More actions for ${s.name}`}>
                      <MoreHorizontal />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem destructive onSelect={() => setDel(s)}>
                      <Trash2 /> Remove connection
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      <SourceBrowserDialog source={browse} onOpenChange={(o) => !o && setBrowse(null)} />
      <ConfirmDialog
        open={!!del}
        onOpenChange={(o) => !o && setDel(null)}
        title={`Remove ${del?.name ?? "connection"}?`}
        description="Stored credentials are deleted. Datasets already snapshotted from it stay in the workspace."
        confirmLabel="Remove"
        destructive
        pending={remove.isPending}
        onConfirm={() => del && remove.mutate(del.id)}
      />
    </Panel>
  );
}

function DataPageInner() {
  const { id: ws, href, canEdit } = useWorkspace();
  const params = useSearchParams();
  const router = useRouter();
  const [uploadOpen, setUploadOpen] = React.useState(params.get("upload") === "1");
  const [connectOpen, setConnectOpen] = React.useState(params.get("connect") === "1");
  const list = useQuery({ queryKey: qk.datasets(ws), queryFn: () => datasets.list(ws) });
  const rules = useQuery({
    queryKey: ["ws", ws, "quality", "rules", null],
    queryFn: () => quality.rules(ws),
    retry: false,
  });
  const dq = React.useMemo(() => (rules.data ? summarizeRules(rules.data) : null), [rules.data]);

  React.useEffect(() => {
    if (params.get("upload") === "1") setUploadOpen(true);
  }, [params]);

  const closeUpload = (o: boolean) => {
    setUploadOpen(o);
    if (!o && params.get("upload")) router.replace(href("/data"));
  };

  const totalRows = (list.data ?? []).reduce((s, d) => s + (d.row_count || 0), 0);
  const failing = dq ? [...dq.values()].reduce((s, v) => s + v.failing, 0) : 0;

  return (
    <Page>
      <PageHeader
        title="Data"
        description="Datasets in this workspace, their profiles, quality and relationships."
        meta={
          list.data?.length ? (
            <>
              <span className="tabular">{formatInt(list.data.length)} datasets</span>
              <span className="tabular">{formatInt(totalRows)} rows</span>
              {failing ? <span className="text-negative">{failing} failing quality rules</span> : null}
            </>
          ) : undefined
        }
        actions={
          <>
            <Button asChild>
              <Link href={href("/data/relationships")}>
                <GitMerge /> Relationships
              </Link>
            </Button>
            <Button asChild>
              <Link href={href("/data/quality")}>
                <ShieldCheck /> Quality
              </Link>
            </Button>
            {canEdit ? (
              <>
                <Button onClick={() => setConnectOpen(true)}>
                  <PlugZap /> Connect database
                </Button>
                <Button variant="primary" onClick={() => setUploadOpen(true)}>
                  <FileUp /> Upload file
                </Button>
              </>
            ) : null}
          </>
        }
      />
      <PageBody className="flex flex-col gap-4">
        {list.isLoading ? (
          <LoadingState rows={6} />
        ) : list.error ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : !list.data?.length ? (
          <Panel>
            <EmptyState
              icon={Database}
              title="No datasets yet"
              description="Load the Summit Supply Co. demo to explore a realistic, messy B2B dataset with planted analytical stories, or upload your own CSV, Excel, Parquet or JSON file."
              action={<LoadDemoButton workspaceId={ws} />}
              secondary={
                canEdit ? (
                  <Button onClick={() => setUploadOpen(true)}>
                    <FileUp /> Upload a file
                  </Button>
                ) : undefined
              }
            />
          </Panel>
        ) : (
          <DatasetsTable items={list.data} dq={dq} />
        )}
        <SourcesPanel onConnect={() => setConnectOpen(true)} />
      </PageBody>
      <UploadWizard open={uploadOpen} onOpenChange={closeUpload} />
      <ConnectDatabaseDialog open={connectOpen} onOpenChange={setConnectOpen} />
    </Page>
  );
}

export default function DataPage() {
  return (
    <Suspense fallback={<LoadingState variant="block" />}>
      <DataPageInner />
    </Suspense>
  );
}
