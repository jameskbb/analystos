"use client";
import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { GitFork, Lightbulb, Save, Trash2, Undo2 } from "lucide-react";
import type { DriverEdge } from "@/lib/api/types";
import { metricTrees, metrics as metricsApi } from "@/lib/api/resources/semantic";
import { ApiError } from "@/lib/api/client";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader, Panel } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { NativeSelect } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states/states";
import { LoadDemoButton } from "@/components/shell/use-load-demo";
import { SemanticNav } from "@/components/semantic/semantic-nav";
import { MetricTreeEditor } from "@/components/semantic/metric-tree-editor";
import { RELATIONS, edgeKey, isSuggestedEdge, mergeSuggestions, relationMeta } from "@/components/semantic/metric-utils";

function TreeWorkspace({ root }: { root: string }) {
  const { id: ws, href, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const all = useQuery({ queryKey: qk.metrics(ws), queryFn: () => metricsApi.list(ws) });
  const tree = useQuery({
    queryKey: [...qk.metricTrees(ws), root],
    queryFn: async () => {
      try {
        return await metricTrees.get(ws, root);
      } catch (e) {
        if (e instanceof ApiError && e.isNotFound) return null;
        throw e;
      }
    },
  });
  const [edges, setEdges] = React.useState<DriverEdge[]>([]);
  const [dirty, setDirty] = React.useState(false);
  const [rationale, setRationale] = React.useState<string[]>([]);
  const [pendingKey, setPendingKey] = React.useState<string | null>(null);
  const [deleting, setDeleting] = React.useState(false);

  React.useEffect(() => {
    if (tree.data !== undefined) {
      setEdges(tree.data?.nodes ?? []);
      setDirty(false);
      setRationale([]);
    }
  }, [tree.data]);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: qk.metricTrees(ws) });
    void qc.invalidateQueries({ queryKey: qk.semantic(ws, "model") });
  };

  const suggest = useMutation({
    mutationFn: () => metricTrees.suggest(ws, root),
    onSuccess: (s) => {
      const merged = mergeSuggestions(edges, s.edges);
      const added = merged.length - edges.length;
      setEdges(merged);
      setRationale(s.rationale);
      if (added) toast.message(`${added} suggested driver${added > 1 ? "s" : ""} added`, { description: "Suggestions are not used by investigations until approved." });
      else toast.message("No new suggestions", { description: "Every suggested driver is already in the tree." });
    },
    onError: (e: Error) => toast.error("Could not suggest drivers", { description: e.message }),
  });

  const save = useMutation({
    mutationFn: () => metricTrees.save(ws, { root_metric: root, nodes: edges, name: tree.data?.name ?? null, description: tree.data?.description ?? null }),
    onSuccess: (t) => {
      qc.setQueryData([...qk.metricTrees(ws), root], t);
      refresh();
      toast.success("Driver tree saved");
    },
  });

  const decide = useMutation({
    mutationFn: ({ edge, decision }: { edge: DriverEdge; decision: "approve" | "reject" }) =>
      metricTrees.decideEdge(ws, root, { parent: edge.parent, child: edge.child, decision }),
    onMutate: ({ edge }) => setPendingKey(edgeKey(edge)),
    onSettled: () => setPendingKey(null),
    onSuccess: (t, { edge, decision }) => {
      // Keep suggestions that have not been decided yet (they only live in the editor until approved).
      const persisted = new Set(t.nodes.map(edgeKey));
      const pendingSuggestions = edges.filter((e) => isSuggestedEdge(e) && !persisted.has(edgeKey(e)) && edgeKey(e) !== edgeKey(edge));
      qc.setQueryData([...qk.metricTrees(ws), root], t);
      setEdges([...t.nodes, ...pendingSuggestions]);
      setDirty(false);
      refresh();
      toast.success(decision === "approve" ? `Approved ${edge.parent} → ${edge.child}` : `Rejected ${edge.parent} → ${edge.child}`);
    },
    onError: (e: Error) => toast.error("Decision failed", { description: e.message }),
  });

  const remove = useMutation({
    mutationFn: () => metricTrees.remove(ws, root),
    onSuccess: () => {
      qc.setQueryData([...qk.metricTrees(ws), root], null);
      refresh();
      setDeleting(false);
      toast.success("Driver tree deleted");
    },
  });

  const onDecide = (edge: DriverEdge, decision: "approve" | "reject") => {
    if (dirty) {
      // Unsaved local edits: apply locally and let Save persist the whole tree.
      setEdges((cur) =>
        decision === "reject"
          ? cur.filter((e) => edgeKey(e) !== edgeKey(edge))
          : cur.map((e) => (edgeKey(e) === edgeKey(edge) ? { ...e, approved: true, suggested: false } : e)),
      );
      return;
    }
    decide.mutate({ edge, decision });
  };

  const options = (all.data ?? []).map((m) => ({ id: m.id, label: m.label ?? m.id }));
  const approved = edges.filter((e) => !isSuggestedEdge(e)).length;
  const suggested = edges.length - approved;

  if (tree.isLoading || all.isLoading) return <LoadingState variant="block" label="Loading driver tree" />;
  if (tree.error) return <ErrorState error={tree.error} onRetry={() => void tree.refetch()} />;

  return (
    <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_300px]">
      <Panel
        title={
          <span className="flex items-center gap-2">
            {options.find((o) => o.id === root)?.label ?? root}
            <Badge tone="positive">{approved} approved</Badge>
            {suggested ? <Badge tone="warning">{suggested} suggested</Badge> : null}
            {dirty ? <Badge tone="accent">Unsaved</Badge> : null}
          </span>
        }
        description={tree.data ? `Version ${tree.data.version_no ?? 1}` : "No tree saved for this metric yet"}
        actions={
          canEdit ? (
            <>
              <Button size="xs" onClick={() => suggest.mutate()} disabled={suggest.isPending}>
                <Lightbulb /> {suggest.isPending ? "Suggesting…" : "Suggest drivers"}
              </Button>
              {dirty ? (
                <Button size="xs" variant="ghost" onClick={() => {
                    setEdges(tree.data?.nodes ?? []);
                    setDirty(false);
                  }}
                >
                  <Undo2 /> Discard
                </Button>
              ) : null}
              <Button size="xs" variant="primary" onClick={() => save.mutate()} disabled={!dirty || save.isPending}>
                <Save /> {save.isPending ? "Saving…" : "Save"}
              </Button>
              {tree.data ? (
                <Button size="icon-xs" variant="ghost" onClick={() => setDeleting(true)} aria-label="Delete tree">
                  <Trash2 />
                </Button>
              ) : null}
            </>
          ) : null
        }
      >
        <div className="flex flex-col gap-3 p-3">
          <InlineError error={save.error} />
          {edges.length === 0 ? (
            <EmptyState
              compact
              icon={GitFork}
              title="This metric has no drivers yet"
              description="Add drivers by hand, or let AnalystOS suggest them from metric definitions (e.g. Revenue = Orders × AOV). Suggestions must be approved before investigations use them."
              action={
                canEdit ? (
                  <Button variant="primary" onClick={() => suggest.mutate()} disabled={suggest.isPending}>
                    <Lightbulb /> Suggest drivers
                  </Button>
                ) : null
              }
            />
          ) : null}
          <MetricTreeEditor
            root={root}
            edges={edges}
            metrics={options}
            readOnly={!canEdit}
            pendingKey={pendingKey}
            metricHref={(id) => href(`/metrics/${encodeURIComponent(id)}`)}
            onDecide={onDecide}
            onRemove={(edge) => {
              setEdges((cur) => cur.filter((e) => edgeKey(e) !== edgeKey(edge)));
              setDirty(true);
            }}
            onAdd={(edge) => {
              setEdges((cur) => [...cur, edge]);
              setDirty(true);
            }}
          />
        </div>
      </Panel>
      <div className="flex flex-col gap-3">
        <Panel title="How trees are used">
          <div className="flex flex-col gap-2 p-3 text-sm text-fg-muted">
            <p>
              Investigations decompose a change along <span className="font-medium text-fg">approved</span> edges only: additive trees split
              exactly, multiplicative trees use log-mean (LMDI) attribution so effects sum to the total, and ratios separate numerator and
              denominator effects.
            </p>
            <p>
              <span className="rounded-sm border border-dashed border-hypothesis bg-hypothesis-soft px-1 text-hypothesis">Suggested</span> edges
              are proposals. They are not canonical until approved.
            </p>
          </div>
        </Panel>
        <Panel title="Relations">
          <ul className="flex flex-col gap-1.5 p-3 text-sm">
            {RELATIONS.map((r) => (
              <li key={r} className="flex items-start gap-2">
                <span className="mt-0.5 inline-flex h-[18px] min-w-9 items-center justify-center rounded-sm border border-border-strong bg-bg-subtle px-1 font-mono text-2xs">
                  {relationMeta(r).symbol}
                </span>
                <span>
                  <span className="font-medium">{relationMeta(r).label}.</span> <span className="text-fg-muted">{relationMeta(r).description}</span>
                </span>
              </li>
            ))}
          </ul>
        </Panel>
        {rationale.length ? (
          <Panel title="Why these suggestions">
            <ul className="flex list-disc flex-col gap-1 p-3 pl-7 text-sm text-fg-muted">
              {rationale.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </Panel>
        ) : null}
      </div>
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title="Delete this driver tree?"
        description="Investigations of this metric will fall back to dimension breakdowns only."
        confirmLabel="Delete"
        destructive
        pending={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}

function TreesScreen() {
  const { id: ws, href } = useWorkspace();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const all = useQuery({ queryKey: qk.metrics(ws), queryFn: () => metricsApi.list(ws) });
  const trees = useQuery({ queryKey: qk.metricTrees(ws), queryFn: () => metricTrees.list(ws) });
  const selected = params.get("root") ?? trees.data?.[0]?.root_metric ?? all.data?.[0]?.id ?? "";
  const setRoot = (r: string) => router.replace(`${pathname}?root=${encodeURIComponent(r)}`, { scroll: false });
  const withTree = new Set((trees.data ?? []).map((t) => t.root_metric));

  return (
    <Page>
      <PageHeader
        title="Driver trees"
        description="How metrics decompose into drivers. Investigations use approved trees to explain changes."
        meta={<SemanticNav />}
        actions={
          all.data?.length ? (
            <label className="flex items-center gap-2 text-sm text-fg-muted">
              Root metric
              <NativeSelect value={selected} onChange={(e) => setRoot(e.target.value)} className="w-56" aria-label="Root metric">
                {all.data.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label ?? m.id}
                    {withTree.has(m.id) ? "  (tree)" : ""}
                  </option>
                ))}
              </NativeSelect>
            </label>
          ) : null
        }
      />
      <PageBody>
        {all.isLoading || trees.isLoading ? (
          <LoadingState />
        ) : all.error ? (
          <ErrorState error={all.error} onRetry={() => void all.refetch()} />
        ) : !all.data?.length ? (
          <EmptyState
            icon={GitFork}
            title="Define metrics before building driver trees"
            description="Driver trees connect metrics, for example Revenue → Orders × Average order value."
            action={<LoadDemoButton workspaceId={ws} />}
            secondary={
              <Button asChild>
                <Link href={href("/metrics?new=1")}>Define a metric</Link>
              </Button>
            }
          />
        ) : (
          <div className="flex flex-col gap-3">
            {trees.data?.length ? (
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-fg-subtle">
                Saved trees:
                {trees.data.map((t) => (
                  <button
                    key={t.root_metric}
                    type="button"
                    onClick={() => setRoot(t.root_metric)}
                    className={`rounded-sm border px-1.5 py-0.5 ${t.root_metric === selected ? "border-accent bg-accent-soft text-accent-soft-fg" : "border-border hover:border-border-strong"}`}
                    aria-pressed={t.root_metric === selected}
                  >
                    {all.data.find((m) => m.id === t.root_metric)?.label ?? t.root_metric}
                    <span className="ml-1 tabular text-fg-faint">{t.nodes.length}</span>
                  </button>
                ))}
              </div>
            ) : null}
            {selected ? <TreeWorkspace key={selected} root={selected} /> : null}
          </div>
        )}
      </PageBody>
    </Page>
  );
}

export default function TreesPage() {
  return (
    <Suspense fallback={<LoadingState variant="block" />}>
      <TreesScreen />
    </Suspense>
  );
}
