"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Group, Panel, Separator } from "react-resizable-panels";
import { toast } from "sonner";
import { GitCompare, NotebookPen, RotateCw, ChevronRight, MessageSquareText, AlertTriangle } from "lucide-react";
import type { ArtifactRecord, Investigation } from "@/lib/api/types";
import { investigations, isJobAccepted, notebooks, waitForJob } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { StatusBadge } from "@/components/analysis/status";
import { Statement } from "@/components/analysis/statement";
import { ExportMenu } from "@/components/export/export-menu";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/input";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/states";
import { Spinner } from "@/components/ui/spinner";
import { formatRelative, pluralize } from "@/lib/format";
import { InvestigationTreeView } from "./investigation-tree";
import { NodeDetail } from "./node-detail";
import { EvidencePanel } from "./evidence-panel";
import { Inspector, type InspectorTab } from "./inspector";
import { CommandBar } from "./command-bar";
import { parentFormatOf } from "./model";
import { PlanEditor } from "./plan-editor";
import { DiffView } from "./diff-view";
import {
  RUNNING_STATUSES,
  contextChips,
  defaultExpanded,
  followUpItems,
  headline,
  needsPlanReview,
  normalizeTree,
  type NormTree,
} from "./model";

function useMediaQuery(query: string): boolean {
  const [match, setMatch] = React.useState(true);
  React.useEffect(() => {
    const mq = window.matchMedia(query);
    setMatch(mq.matches);
    const on = () => setMatch(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

function PanelTitle({ children, actions }: { children: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="flex h-8 shrink-0 items-center justify-between gap-2 border-b border-border bg-bg-subtle px-3">
      <h2 className="text-2xs font-semibold tracking-wider text-fg-subtle uppercase">{children}</h2>
      {actions}
    </div>
  );
}

function DiffDialog({ open, onOpenChange, investigation, onSelectNode }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  investigation: Investigation;
  onSelectNode: (id: string) => void;
}) {
  const { id: ws } = useWorkspace();
  const runs = useQuery({
    queryKey: qk.investigationPart(ws, investigation.id, "runs", investigation.run_count),
    queryFn: () => investigations.runs(ws, investigation.id),
    enabled: open,
  });
  const nos = (runs.data ?? []).map((r) => r.run_no).sort((a, b) => a - b);
  const [range, setRange] = React.useState<{ from?: number; to?: number }>({});
  const from = range.from ?? nos[nos.length - 2];
  const to = range.to ?? nos[nos.length - 1];
  const diff = useQuery({
    queryKey: qk.investigationPart(ws, investigation.id, "diff", { from, to }),
    // The API addresses runs by id; the UI shows run numbers.
    queryFn: () => {
      const idOf = (n: number | undefined) => (runs.data ?? []).find((r) => r.run_no === n)?.id;
      return investigations.diff(ws, investigation.id, { from_run: idOf(from), to_run: idOf(to) });
    },
    enabled: open && runs.isSuccess && (nos.length < 2 || (from !== undefined && to !== undefined)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>What changed since the previous run</DialogTitle>
          <DialogDescription>Compares findings between runs and shows which data or metric definitions changed underneath.</DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-3">
          {nos.length >= 2 ? (
            <div className="flex items-center gap-2 text-xs">
              <span className="text-fg-subtle">Compare run</span>
              <NativeSelect aria-label="From run" className="h-6 w-20" value={from} onChange={(e) => setRange((r) => ({ ...r, from: Number(e.target.value) }))}>
                {nos.map((n) => (
                  <option key={n} value={n}>
                    #{n}
                  </option>
                ))}
              </NativeSelect>
              <span className="text-fg-subtle">with run</span>
              <NativeSelect aria-label="To run" className="h-6 w-20" value={to} onChange={(e) => setRange((r) => ({ ...r, to: Number(e.target.value) }))}>
                {nos.map((n) => (
                  <option key={n} value={n}>
                    #{n}
                  </option>
                ))}
              </NativeSelect>
            </div>
          ) : null}
          {diff.isLoading || runs.isLoading ? (
            <LoadingState />
          ) : diff.error ? (
            <ErrorState error={diff.error} compact onRetry={() => void diff.refetch()} />
          ) : diff.data ? (
            <DiffView
              diff={diff.data}
              onSelectNode={(id) => {
                onSelectNode(id);
                onOpenChange(false);
              }}
            />
          ) : (
            <p className="text-sm text-fg-subtle">Rerun the investigation to compare results.</p>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

function WorkstationPanels({
  investigation,
  tree,
  artifacts,
  artifactsLoading,
  canEdit,
  selectedId,
  setSelectedId,
  expanded,
  setExpanded,
  inspectorTab,
  setInspectorTab,
  inspectorArtifact,
  setInspectorArtifact,
}: {
  investigation: Investigation;
  tree: NormTree;
  artifacts: Map<string, ArtifactRecord>;
  artifactsLoading: boolean;
  canEdit: boolean;
  selectedId: string | null;
  setSelectedId: (id: string) => void;
  expanded: Set<string>;
  setExpanded: (s: Set<string>) => void;
  inspectorTab: InspectorTab;
  setInspectorTab: (t: InspectorTab) => void;
  inspectorArtifact: string | null;
  setInspectorArtifact: (id: string) => void;
}) {
  const wide = useMediaQuery("(min-width: 1024px)");
  const node = selectedId ? tree.nodes.get(selectedId) ?? null : null;
  const nodeArtifacts = (node?.artifact_ids ?? []).map((a) => artifacts.get(a)).filter((a): a is ArtifactRecord => !!a);
  const filters = contextChips(investigation.interpretation, node);

  const toggle = (id: string) => {
    const next = new Set(expanded);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setExpanded(next);
  };
  const expandAllBelow = (id: string) => {
    const next = new Set(expanded);
    const rec = (n: string) => {
      const x = tree.nodes.get(n);
      if (!x || !x.children.length) return;
      next.add(n);
      x.children.forEach(rec);
    };
    rec(id);
    setExpanded(next);
  };

  const treePane = (
    <div className="flex h-full min-h-0 flex-col">
      <PanelTitle
        actions={
          <span className="text-2xs text-fg-subtle tabular">{pluralize(tree.nodes.size, "node")}</span>
        }
      >
        Investigation tree
      </PanelTitle>
      <div className="min-h-0 flex-1 overflow-auto px-1 scrollbar-thin">
        <InvestigationTreeView tree={tree} selectedId={selectedId} onSelect={setSelectedId} expanded={expanded} onExpandedChange={setExpanded} />
      </div>
    </div>
  );
  const centerPane = (
    <div className="h-full min-h-0 overflow-auto scrollbar-thin" aria-label="Selected node analysis">
      {node ? (
        <NodeDetail
          investigation={investigation}
          node={node}
          artifacts={nodeArtifacts}
          filters={filters}
          expanded={expanded.has(node.id)}
          onToggleExpanded={() => toggle(node.id)}
          onExpandAll={() => expandAllBelow(node.id)}
          canEdit={canEdit}
          parentFormat={parentFormatOf(tree, node)}
        />
      ) : (
        <EmptyState compact title="Select a node" description="Choose a node in the tree to see its calculation, chart and evidence." />
      )}
      {artifactsLoading && node?.artifact_ids.length ? <LoadingState variant="inline" label="Loading artifacts" className="px-4 pb-4" /> : null}
    </div>
  );
  const evidencePane = (
    <div className="flex h-full min-h-0 flex-col">
      <PanelTitle>Evidence &amp; methodology</PanelTitle>
      <div className="min-h-0 flex-1 overflow-auto scrollbar-thin">
        <EvidencePanel node={node} artifacts={nodeArtifacts} investigation={investigation} hypotheses={investigation.hypotheses ?? []} />
      </div>
    </div>
  );
  const inspectorPane = (
    <Inspector
      artifacts={nodeArtifacts}
      tab={inspectorTab}
      onTabChange={setInspectorTab}
      artifactId={inspectorArtifact}
      onArtifactChange={setInspectorArtifact}
      format={node?.format ?? null}
    />
  );

  if (!wide) {
    return (
      <Tabs defaultValue="tree" className="flex min-h-0 flex-1 flex-col">
        <TabsList className="px-2">
          <TabsTrigger value="tree">Tree</TabsTrigger>
          <TabsTrigger value="node">Analysis</TabsTrigger>
          <TabsTrigger value="evidence">Evidence</TabsTrigger>
          <TabsTrigger value="inspector">SQL &amp; results</TabsTrigger>
        </TabsList>
        <TabsContent value="tree" className="min-h-0 flex-1">{treePane}</TabsContent>
        <TabsContent value="node" className="min-h-0 flex-1">{centerPane}</TabsContent>
        <TabsContent value="evidence" className="min-h-0 flex-1">{evidencePane}</TabsContent>
        <TabsContent value="inspector" className="min-h-0 flex-1">{inspectorPane}</TabsContent>
      </Tabs>
    );
  }

  return (
    <Group orientation="horizontal" className="min-h-0 flex-1" id="aos-investigation-h">
      <Panel id="tree" defaultSize="26" minSize="16" className="min-w-0">
        {treePane}
      </Panel>
      <Separator className="w-px" aria-label="Resize tree panel" />
      <Panel id="center" defaultSize="50" minSize="30" className="min-w-0">
        <Group orientation="vertical" className="h-full" id="aos-investigation-v">
          <Panel id="node" defaultSize="64" minSize="25">
            {centerPane}
          </Panel>
          <Separator className="h-px" aria-label="Resize inspector panel" />
          <Panel id="inspector" defaultSize="36" minSize="12" collapsible>
            {inspectorPane}
          </Panel>
        </Group>
      </Panel>
      <Separator className="w-px" aria-label="Resize evidence panel" />
      <Panel id="evidence" defaultSize="24" minSize="14" className="min-w-0">
        {evidencePane}
      </Panel>
    </Group>
  );
}

/** The flagship investigation workstation (spec §72). */
export function Workstation({ investigationId }: { investigationId: string }) {
  const { id: ws, href, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const router = useRouter();
  const inv = useQuery({
    queryKey: qk.investigation(ws, investigationId),
    queryFn: () => investigations.get(ws, investigationId),
    refetchInterval: (q) => (q.state.data && RUNNING_STATUSES.has(q.state.data.status) ? 1500 : false),
  });
  const data = inv.data;
  const tree = React.useMemo(() => normalizeTree(data?.tree, data?.node_findings), [data?.tree, data?.node_findings]);
  const hasTree = tree.nodes.size > 0;
  const arts = useQuery({
    queryKey: qk.investigationPart(ws, investigationId, "artifacts", data?.run_count ?? 0),
    queryFn: () => investigations.artifacts(ws, investigationId),
    enabled: hasTree,
  });
  // Tree nodes reference engine artifact ids; index by both so either form resolves.
  const artifactMap = React.useMemo(() => {
    const m = new Map<string, ArtifactRecord>();
    for (const a of arts.data ?? []) {
      m.set(a.id, a);
      if (a.engine_id) m.set(a.engine_id, a);
    }
    return m;
  }, [arts.data]);

  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());
  const [inspectorTab, setInspectorTab] = React.useState<InspectorTab>("sql");
  const [inspectorArtifact, setInspectorArtifact] = React.useState<string | null>(null);
  const [diffOpen, setDiffOpen] = React.useState(false);
  const commandRef = React.useRef<HTMLInputElement>(null);
  const initializedFor = React.useRef<string | null>(null);

  React.useEffect(() => {
    if (!hasTree || !tree.rootId) return;
    const key = `${investigationId}:${data?.run_count}`;
    if (initializedFor.current === key) {
      if (selectedId && !tree.nodes.has(selectedId)) setSelectedId(tree.rootId);
      return;
    }
    initializedFor.current = key;
    setExpanded(defaultExpanded(tree, 2));
    setSelectedId((s) => (s && tree.nodes.has(s) ? s : tree.rootId));
  }, [hasTree, tree, investigationId, data?.run_count, selectedId]);

  // "/" focuses the command bar.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      e.preventDefault();
      commandRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const rerun = useMutation({
    mutationFn: async () => {
      const res = await investigations.rerun(ws, investigationId);
      if (isJobAccepted(res)) {
        await waitForJob(res);
        return investigations.get(ws, investigationId);
      }
      return res;
    },
    onSuccess: (next) => {
      qc.setQueryData(qk.investigation(ws, investigationId), next);
      void qc.invalidateQueries({ queryKey: ["ws", ws, "investigations"] });
      if (next.run_count > 1) setDiffOpen(true);
    },
    onError: (e: Error) => toast.error("Rerun failed", { description: e.message }),
  });

  const toNotebook = useMutation({
    mutationFn: () => notebooks.fromInvestigation(ws, investigationId),
    onSuccess: (nb) => router.push(href(`/notebooks/${nb.id}`)),
    onError: (e: Error) => toast.error("Could not export to a notebook", { description: e.message }),
  });

  if (inv.isLoading) return <LoadingState variant="block" label="Loading investigation" className="h-full" />;
  if (inv.error) return <ErrorState error={inv.error} onRetry={() => void inv.refetch()} className="h-full" />;
  if (!data) return null;

  const running = RUNNING_STATUSES.has(data.status);
  const answer = headline(data);
  const showPlan = needsPlanReview(data);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-border px-4 py-2.5">
        <nav aria-label="Breadcrumb" className="mb-0.5 flex items-center gap-1 text-xs text-fg-subtle">
          <Link href={href("/investigate")} className="hover:text-fg">
            Investigations
          </Link>
          <ChevronRight className="size-3" />
          <span className="truncate">{data.title || data.question}</span>
        </nav>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <MessageSquareText className="size-4 shrink-0 text-fg-subtle" aria-hidden />
            <h1 className="truncate text-base font-semibold">{data.question}</h1>
            <StatusBadge status={data.status} />
            <span className="hidden text-xs text-fg-subtle sm:inline">
              {data.run_count ? `${pluralize(data.run_count, "run")} · ` : ""}
              {formatRelative(data.completed_at ?? data.updated_at ?? data.created_at)}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5 no-print">
            <Button size="sm" onClick={() => rerun.mutate()} disabled={!canEdit || rerun.isPending || running || !hasTree}>
              {rerun.isPending ? <Spinner /> : <RotateCw />} Re-run
            </Button>
            <Button size="sm" onClick={() => setDiffOpen(true)} disabled={data.run_count < 2}>
              <GitCompare /> Diff
            </Button>
            <Button size="sm" onClick={() => toNotebook.mutate()} disabled={!hasTree || toNotebook.isPending}>
              <NotebookPen /> Notebook
            </Button>
            <ExportMenu target={{ kind: "investigation", id: data.id }} formats={["md", "html", "pdf", "ipynb", "json"]} filename={`investigation-${data.id.slice(0, 8)}`} allowPrint />
          </div>
        </div>
        {hasTree ? (
          <div className="mt-2 grid gap-2 lg:grid-cols-[minmax(0,1fr)_minmax(320px,440px)]">
            {answer ? (
              <div className="min-w-0 rounded border border-border bg-bg-subtle px-3 py-2">
                <Statement type="observation" showBadge={false} className="border-l-accent">
                  {answer}
                </Statement>
              </div>
            ) : (
              <div />
            )}
            <CommandBar
              ref={commandRef}
              investigationId={data.id}
              nodeId={selectedId}
              onSelectNode={setSelectedId}
              onShowSql={(aid) => {
                setInspectorTab("sql");
                if (aid) setInspectorArtifact(aid);
              }}
              followUps={followUpItems(data)}
            />
          </div>
        ) : null}
        {data.failures?.length ? (
          <details className="mt-2 text-xs text-warning">
            <summary className="cursor-pointer">
              <AlertTriangle className="mr-1 inline size-3" /> {pluralize(data.failures.length, "test")} failed and {data.failures.length === 1 ? "was" : "were"} excluded from the tree
            </summary>
            <ul className="mt-1 list-disc pl-5">
              {data.failures.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </header>

      {data.status === "failed" && !hasTree ? (
        <ErrorState
          error={new Error(data.error || "The investigation failed before producing results.")}
          title="Investigation failed"
          onRetry={canEdit ? () => rerun.mutate() : undefined}
          className="flex-1"
        />
      ) : running && !hasTree ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-sm text-fg-subtle" role="status" aria-live="polite">
          <Spinner className="size-5" />
          Executing analytical tests against the data…
          <span className="text-xs">Every number in the tree will come from an executed query.</span>
        </div>
      ) : showPlan ? (
        <div className="min-h-0 flex-1 overflow-auto scrollbar-thin">
          <PlanEditor investigation={data} canEdit={canEdit} />
        </div>
      ) : hasTree ? (
        <WorkstationPanels
          investigation={data}
          tree={tree}
          artifacts={artifactMap}
          artifactsLoading={arts.isLoading}
          canEdit={canEdit}
          selectedId={selectedId}
          setSelectedId={setSelectedId}
          expanded={expanded}
          setExpanded={setExpanded}
          inspectorTab={inspectorTab}
          setInspectorTab={setInspectorTab}
          inspectorArtifact={inspectorArtifact}
          setInspectorArtifact={setInspectorArtifact}
        />
      ) : (
        <EmptyState
          title="No results yet"
          description="This investigation has not produced a tree. Review the plan and run it."
          action={
            <Button variant="primary" onClick={() => rerun.mutate()} disabled={!canEdit}>
              Run investigation
            </Button>
          }
        />
      )}
      {arts.error && hasTree ? (
        <div className="shrink-0 border-t border-border px-4 py-1 text-xs text-negative">Artifacts failed to load: {(arts.error as Error).message}</div>
      ) : null}
      <DiffDialog open={diffOpen} onOpenChange={setDiffOpen} investigation={data} onSelectNode={setSelectedId} />
    </div>
  );
}
