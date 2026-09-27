"use client";
import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, Download, Pencil, Plus, Search, Sigma, Trash2, Upload, Columns3, Boxes, FileCode2 } from "lucide-react";
import type { Dimension, Entity, Metric } from "@/lib/api/types";
import { dimensions as dimensionsApi, metrics as metricsApi, semantic } from "@/lib/api/resources/semantic";
import { downloadBlob } from "@/lib/api/client";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader, Panel } from "@/components/shell/page";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input, Textarea } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { EmptyState, ErrorState, InlineError, LoadingState, QueryState } from "@/components/states/states";
import { CodeEditor } from "@/components/code/code-editor";
import { LoadDemoButton } from "@/components/shell/use-load-demo";
import { MetricFormDialog, IssueList } from "@/components/semantic/metric-form-dialog";
import { DimensionDialog, EntityDialog } from "@/components/semantic/semantic-dialogs";
import { SemanticNav } from "@/components/semantic/semantic-nav";
import { ListTable, Td, Th, Tr } from "@/components/semantic/list-table";
import { findSynonymConflicts, formulaSummary } from "@/components/semantic/metric-utils";
import { formatDateTime, formatRelative } from "@/lib/format";

type Tab = "metrics" | "dimensions" | "entities" | "model";
const TABS: Tab[] = ["metrics", "dimensions", "entities", "model"];

function useParamSetter() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  return React.useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(params.toString());
      Object.entries(patch).forEach(([k, v]) => (v === null || v === "" ? next.delete(k) : next.set(k, v)));
      const qs = next.toString();
      router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false });
    },
    [params, pathname, router],
  );
}

const matches = (q: string, ...fields: (string | null | undefined | string[])[]) => {
  if (!q) return true;
  const needle = q.toLowerCase();
  return fields.flat().some((f) => (f ?? "").toString().toLowerCase().includes(needle));
};

function MetricsTab({ q, onEdit, onNew }: { q: string; onEdit: (m: Metric) => void; onNew: () => void }) {
  const { id: ws, href, canEdit } = useWorkspace();
  const query = useQuery({ queryKey: qk.metrics(ws), queryFn: () => metricsApi.list(ws) });
  const conflicts = React.useMemo(() => findSynonymConflicts(query.data ?? []), [query.data]);
  return (
    <QueryState
      query={query}
      empty={
        <EmptyState
          icon={Sigma}
          title="No metrics defined yet"
          description="Metrics are the canonical definitions every investigation, dashboard and report uses. Load the demo for a complete model, or define your first metric."
          action={<LoadDemoButton workspaceId={ws} />}
          secondary={
            canEdit ? (
              <Button onClick={onNew}>
                <Plus /> Define a metric
              </Button>
            ) : null
          }
        />
      }
    >
      {(list) => {
        const rows = list.filter((m) => matches(q, m.id, m.label, m.description, m.tags, m.synonyms, m.owner));
        return (
          <div className="flex flex-col gap-3">
            {conflicts.length ? (
              <div role="note" className="flex items-start gap-2 rounded border border-warning/30 bg-warning-soft px-3 py-2 text-sm text-warning">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                <div>
                  <span className="font-medium">Ambiguous terms.</span> These words map to more than one metric, so AnalystOS will ask
                  which one you mean instead of guessing:{" "}
                  {conflicts.map((c, i) => (
                    <span key={c.term}>
                      {i ? "; " : ""}
                      <span className="font-medium">“{c.term}”</span> → {c.metricIds.join(", ")}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
            <ListTable label="Metrics">
              <thead>
                <tr>
                  <Th>Metric</Th>
                  <Th>Kind</Th>
                  <Th>Definition</Th>
                  <Th>Format</Th>
                  <Th>Owner</Th>
                  <Th>Tags</Th>
                  <Th className="text-right">Version</Th>
                  <Th>
                    <span className="sr-only">Actions</span>
                  </Th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <Td colSpan={8} className="py-6 text-center text-fg-subtle">
                      No metrics match “{q}”.
                    </Td>
                  </tr>
                ) : (
                  rows.map((m) => (
                    <Tr key={m.id}>
                      <Td>
                        <div className="flex items-center gap-1.5">
                          <Link href={href(`/metrics/${encodeURIComponent(m.id)}`)} className="font-medium hover:underline">
                            {m.label ?? m.id}
                          </Link>
                          {m.canonical ? <Badge tone="positive">Canonical</Badge> : <Badge tone="outline">Non-canonical</Badge>}
                        </div>
                        <div className="font-mono text-2xs text-fg-subtle">{m.id}</div>
                      </Td>
                      <Td className="text-fg-muted">{m.kind}</Td>
                      <Td className="max-w-80">
                        <code className="block truncate font-mono text-xs" title={formulaSummary(m)}>
                          {formulaSummary(m)}
                        </code>
                      </Td>
                      <Td className="text-fg-muted">{m.format}</Td>
                      <Td className="text-fg-muted">{m.owner ?? "n/a"}</Td>
                      <Td>
                        <div className="flex flex-wrap gap-1">
                          {(m.tags ?? []).map((t) => (
                            <Badge key={t}>{t}</Badge>
                          ))}
                        </div>
                      </Td>
                      <Td className="text-right font-mono text-xs tabular">v{m.version_no ?? m.version}</Td>
                      <Td className="w-8">
                        {canEdit ? (
                          <Button size="icon-xs" variant="ghost" onClick={() => onEdit(m)} aria-label={`Edit ${m.label ?? m.id}`}>
                            <Pencil />
                          </Button>
                        ) : null}
                      </Td>
                    </Tr>
                  ))
                )}
              </tbody>
            </ListTable>
          </div>
        );
      }}
    </QueryState>
  );
}

function DimensionsTab({ q }: { q: string }) {
  const { id: ws, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const query = useQuery({ queryKey: qk.dimensions(ws), queryFn: () => dimensionsApi.list(ws) });
  const [editing, setEditing] = React.useState<Dimension | null>(null);
  const [creating, setCreating] = React.useState(false);
  const [deleting, setDeleting] = React.useState<Dimension | null>(null);
  const del = useMutation({
    mutationFn: (name: string) => dimensionsApi.remove(ws, name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.dimensions(ws) });
      setDeleting(null);
      toast.success("Dimension deleted");
    },
    onError: (e: Error) => toast.error("Could not delete the dimension", { description: e.message }),
  });
  return (
    <>
      <div className="mb-2 flex justify-end">
        {canEdit ? (
          <Button onClick={() => setCreating(true)}>
            <Plus /> New dimension
          </Button>
        ) : null}
      </div>
      <QueryState
        query={query}
        empty={
          <EmptyState
            icon={Columns3}
            title="No dimensions"
            description="Dimensions (region, product, customer, month…) are how metrics can be broken down."
            action={<LoadDemoButton workspaceId={ws} />}
          />
        }
      >
        {(list) => (
          <ListTable label="Dimensions">
            <thead>
              <tr>
                <Th>Dimension</Th>
                <Th>Entity</Th>
                <Th>Expression</Th>
                <Th>Type</Th>
                <Th>Time grains</Th>
                <Th>Synonyms</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {list
                .filter((d) => matches(q, d.name, d.label, d.entity, d.expr, d.synonyms))
                .map((d) => (
                  <Tr key={d.name}>
                    <Td>
                      <div className="font-medium">{d.label || d.name}</div>
                      <div className="font-mono text-2xs text-fg-subtle">{d.name}</div>
                    </Td>
                    <Td className="text-fg-muted">{d.entity}</Td>
                    <Td>
                      <code className="font-mono text-xs">{d.expr}</code>
                    </Td>
                    <Td>
                      <Badge tone={d.type === "time" ? "accent" : "neutral"}>{d.type}</Badge>
                    </Td>
                    <Td className="text-xs text-fg-muted">{(d.time_grains ?? []).join(", ") || "n/a"}</Td>
                    <Td className="text-xs text-fg-muted">{(d.synonyms ?? []).join(", ") || "n/a"}</Td>
                    <Td className="w-16 whitespace-nowrap">
                      {canEdit ? (
                        <>
                          <Button size="icon-xs" variant="ghost" onClick={() => setEditing(d)} aria-label={`Edit ${d.name}`}>
                            <Pencil />
                          </Button>
                          <Button size="icon-xs" variant="ghost" onClick={() => setDeleting(d)} aria-label={`Delete ${d.name}`}>
                            <Trash2 />
                          </Button>
                        </>
                      ) : null}
                    </Td>
                  </Tr>
                ))}
            </tbody>
          </ListTable>
        )}
      </QueryState>
      <DimensionDialog open={creating || !!editing} onOpenChange={(o) => {
          if (!o) {
            setCreating(false);
            setEditing(null);
          }
        }} dimension={editing} />
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete dimension ${deleting?.name}?`}
        description="Metrics and saved analyses that break down by this dimension will no longer be able to use it."
        confirmLabel="Delete"
        destructive
        pending={del.isPending}
        onConfirm={() => deleting && del.mutate(deleting.name)}
      />
    </>
  );
}

function EntitiesTab({ q }: { q: string }) {
  const { id: ws, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const query = useQuery({ queryKey: qk.semantic(ws, "entities"), queryFn: () => semantic.entities(ws) });
  const [editing, setEditing] = React.useState<Entity | null>(null);
  const [creating, setCreating] = React.useState(false);
  const [deleting, setDeleting] = React.useState<Entity | null>(null);
  const del = useMutation({
    mutationFn: (name: string) => semantic.deleteEntity(ws, name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.semantic(ws, "entities") });
      setDeleting(null);
      toast.success("Entity deleted");
    },
    onError: (e: Error) => toast.error("Could not delete the entity", { description: e.message }),
  });
  return (
    <>
      <div className="mb-2 flex justify-end">
        {canEdit ? (
          <Button onClick={() => setCreating(true)}>
            <Plus /> New entity
          </Button>
        ) : null}
      </div>
      <QueryState
        query={query}
        empty={
          <EmptyState
            icon={Boxes}
            title="No entities"
            description="Entities declare each table's grain (one row per order, per order line, per customer) so metrics never double count across joins."
            action={<LoadDemoButton workspaceId={ws} />}
          />
        }
      >
        {(list) => (
          <ListTable label="Entities">
            <thead>
              <tr>
                <Th>Entity</Th>
                <Th>Table</Th>
                <Th>Primary key</Th>
                <Th>Declared grain</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {list
                .filter((en) => matches(q, en.name, en.label, en.table_name, en.grain_description))
                .map((en) => (
                  <Tr key={en.name}>
                    <Td>
                      <div className="font-medium">{en.label || en.name}</div>
                      <div className="font-mono text-2xs text-fg-subtle">{en.name}</div>
                    </Td>
                    <Td>
                      <code className="font-mono text-xs">{en.table_name}</code>
                    </Td>
                    <Td>
                      <code className="font-mono text-xs">{Array.isArray(en.primary_key) ? en.primary_key.join(", ") : en.primary_key}</code>
                    </Td>
                    <Td className="text-fg-muted">{en.grain_description || "n/a"}</Td>
                    <Td className="w-16 whitespace-nowrap">
                      {canEdit ? (
                        <>
                          <Button size="icon-xs" variant="ghost" onClick={() => setEditing(en)} aria-label={`Edit ${en.name}`}>
                            <Pencil />
                          </Button>
                          <Button size="icon-xs" variant="ghost" onClick={() => setDeleting(en)} aria-label={`Delete ${en.name}`}>
                            <Trash2 />
                          </Button>
                        </>
                      ) : null}
                    </Td>
                  </Tr>
                ))}
            </tbody>
          </ListTable>
        )}
      </QueryState>
      <EntityDialog open={creating || !!editing} onOpenChange={(o) => {
          if (!o) {
            setCreating(false);
            setEditing(null);
          }
        }} entity={editing} />
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete entity ${deleting?.name}?`}
        description="Metrics and dimensions defined on this entity must be moved or removed first; the API refuses deletes that would break them."
        confirmLabel="Delete"
        destructive
        pending={del.isPending}
        onConfirm={() => deleting && del.mutate(deleting.name)}
      />
    </>
  );
}

function ImportYamlDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  const [yaml, setYaml] = React.useState("");
  const [note, setNote] = React.useState("Imported from YAML");
  const fileRef = React.useRef<HTMLInputElement>(null);
  const imp = useMutation({
    mutationFn: () => semantic.importYaml(ws, yaml, note),
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: ["ws", ws] });
      const counts = Object.entries(res.counts)
        .map(([k, v]) => `${v} ${k}`)
        .join(", ");
      if (res.issues.some((i) => i.severity === "error")) toast.warning("Imported with errors", { description: counts });
      else toast.success("Semantic model imported", { description: counts });
      if (!res.issues.length) onOpenChange(false);
    },
  });
  React.useEffect(() => {
    if (open) {
      setYaml("");
      imp.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Import semantic model</DialogTitle>
          <DialogDescription>
            Paste or upload YAML. Metrics whose definitions change get new versions; unchanged metrics keep theirs.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".yaml,.yml,text/yaml"
              className="sr-only"
              aria-label="Upload YAML file"
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (f) setYaml(await f.text());
                e.target.value = "";
              }}
            />
            <Button onClick={() => fileRef.current?.click()}>
              <Upload /> Choose file
            </Button>
            <Input value={note} onChange={(e) => setNote(e.target.value)} aria-label="Change note" placeholder="Change note" />
          </div>
          <Textarea
            value={yaml}
            onChange={(e) => setYaml(e.target.value)}
            rows={16}
            className="font-mono text-xs"
            placeholder={"entities:\n  - name: order_line\n    table: order_lines\n    primary_key: order_line_id\nmetrics:\n  - id: revenue\n    ..."}
            aria-label="Semantic model YAML"
          />
          {imp.data ? <IssueList issues={imp.data.issues} /> : null}
          <InlineError error={imp.error} />
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button variant="primary" disabled={!yaml.trim() || imp.isPending} onClick={() => imp.mutate()}>
            {imp.isPending ? "Importing…" : "Import"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ModelTab() {
  const { id: ws, canEdit } = useWorkspace();
  const model = useQuery({ queryKey: qk.semantic(ws, "model"), queryFn: () => semantic.model(ws) });
  const yaml = useQuery({ queryKey: qk.semantic(ws, "yaml"), queryFn: () => semantic.exportYaml(ws) });
  const history = useQuery({ queryKey: qk.semantic(ws, "history"), queryFn: () => semantic.history(ws) });
  const [importing, setImporting] = React.useState(false);
  const counts = model.data
    ? [
        ["entities", model.data.model.entities.length],
        ["dimensions", model.data.model.dimensions.length],
        ["metrics", model.data.model.metrics.length],
        ["relationships", model.data.model.relationships.length],
        ["trees", model.data.model.metric_trees.length],
        ["glossary terms", model.data.model.glossary.length],
      ]
    : [];
  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_300px]">
      <Panel
        title="Semantic model (YAML)"
        description={model.data ? `Snapshot v${model.data.snapshot_version} · hash ${model.data.content_hash.slice(0, 12)}` : undefined}
        actions={
          <>
            {canEdit ? (
              <Button size="xs" onClick={() => setImporting(true)}>
                <Upload /> Import
              </Button>
            ) : null}
            <Button
              size="xs"
              disabled={!yaml.data}
              onClick={() => yaml.data && downloadBlob(new Blob([yaml.data], { type: "text/yaml" }), "semantic_model.yaml")}
            >
              <Download /> Export
            </Button>
          </>
        }
      >
        {yaml.isLoading ? (
          <LoadingState className="p-3" />
        ) : yaml.error ? (
          <ErrorState error={yaml.error} onRetry={() => void yaml.refetch()} compact />
        ) : (
          <CodeEditor value={yaml.data ?? ""} language="text" readOnly className="max-h-[65vh] min-h-64" ariaLabel="Semantic model YAML" />
        )}
      </Panel>
      <div className="flex flex-col gap-3">
        <Panel title="Model health">
          <div className="p-3">
            {model.isLoading ? (
              <LoadingState rows={3} />
            ) : model.error ? (
              <ErrorState error={model.error} compact onRetry={() => void model.refetch()} />
            ) : (
              <>
                <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
                  {counts.map(([k, v]) => (
                    <React.Fragment key={k}>
                      <dt className="text-fg-subtle">{k}</dt>
                      <dd className="text-right tabular">{v}</dd>
                    </React.Fragment>
                  ))}
                </dl>
                <div className="mt-3">
                  {model.data?.issues.length ? (
                    <IssueList issues={model.data.issues} />
                  ) : (
                    <p className="text-xs text-positive">No validation issues.</p>
                  )}
                </div>
              </>
            )}
          </div>
        </Panel>
        <Panel title="Snapshot history" description="Each change to the model is snapshotted with the metric versions in force.">
          <QueryState
            query={history}
            compact
            loading={<LoadingState className="p-3" rows={3} />}
            empty={<p className="p-3 text-sm text-fg-subtle">No snapshots yet.</p>}
          >
            {(snaps) => (
              <ol className="max-h-80 overflow-auto scrollbar-thin">
                {snaps.map((s) => (
                  <li key={s.id} className="flex items-start gap-2 border-b border-border/70 px-3 py-1.5 last:border-b-0">
                    <FileCode2 className="mt-0.5 size-3.5 shrink-0 text-fg-subtle" />
                    <div className="min-w-0">
                      <div className="truncate text-sm">
                        <span className="font-mono text-xs text-fg-subtle">v{s.version_no}</span> {s.reason || "Model change"}
                      </div>
                      <div className="text-2xs text-fg-subtle" title={formatDateTime(s.created_at)}>
                        {formatRelative(s.created_at)} · {s.content_hash.slice(0, 10)}
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </QueryState>
        </Panel>
      </div>
      <ImportYamlDialog open={importing} onOpenChange={setImporting} />
    </div>
  );
}

function MetricsScreen() {
  const params = useSearchParams();
  const setParams = useParamSetter();
  const { canEdit } = useWorkspace();
  const tabParam = params.get("tab") as Tab | null;
  const tab: Tab = tabParam && TABS.includes(tabParam) ? tabParam : "metrics";
  const [q, setQ] = React.useState(params.get("q") ?? "");
  const [editing, setEditing] = React.useState<Metric | null>(null);
  const creating = params.get("new") === "1";
  const searchRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    const t = setTimeout(() => {
      if ((params.get("q") ?? "") !== q) setParams({ q: q || null });
    }, 250);
    return () => clearTimeout(t);
  }, [q, params, setParams]);

  return (
    <Page>
      <PageHeader
        title="Metrics"
        description="The semantic layer: canonical metrics, dimensions and entity grains shared by every analysis."
        meta={<SemanticNav />}
        actions={
          <>
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-fg-faint" />
              <Input
                ref={searchRef}
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Filter…"
                className="w-48 pl-7"
                aria-label="Filter metrics, dimensions and entities"
              />
            </div>
            {canEdit ? (
              <Button variant="primary" onClick={() => setParams({ new: "1" })}>
                <Plus /> Define metric
              </Button>
            ) : null}
          </>
        }
      />
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: v === "metrics" ? null : v })} className="flex min-h-0 flex-1 flex-col">
        <TabsList className="px-4 sm:px-5">
          <TabsTrigger value="metrics">
            <Sigma /> Metrics
          </TabsTrigger>
          <TabsTrigger value="dimensions">
            <Columns3 /> Dimensions
          </TabsTrigger>
          <TabsTrigger value="entities">
            <Boxes /> Entities
          </TabsTrigger>
          <TabsTrigger value="model">
            <FileCode2 /> Model
          </TabsTrigger>
        </TabsList>
        <PageBody>
          <TabsContent value="metrics">
            <MetricsTab q={q} onEdit={setEditing} onNew={() => setParams({ new: "1" })} />
          </TabsContent>
          <TabsContent value="dimensions">
            <DimensionsTab q={q} />
          </TabsContent>
          <TabsContent value="entities">
            <EntitiesTab q={q} />
          </TabsContent>
          <TabsContent value="model">
            <ModelTab />
          </TabsContent>
        </PageBody>
      </Tabs>
      <MetricFormDialog
        open={creating || !!editing}
        metric={editing}
        onOpenChange={(o) => {
          if (!o) {
            setEditing(null);
            if (creating) setParams({ new: null });
          }
        }}
      />
    </Page>
  );
}

export default function MetricsPage() {
  return (
    <Suspense fallback={<LoadingState variant="block" />}>
      <MetricsScreen />
    </Suspense>
  );
}
