"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Check,
  ChevronsDownUp,
  ChevronsUpDown,
  CircleSlash,
  GitFork,
  Lightbulb,
  MessageSquarePlus,
  MoreHorizontal,
  Pencil,
  RotateCw,
  Flag,
} from "lucide-react";
import type { ArtifactRecord, FilterContextItem, Investigation } from "@/lib/api/types";
import { dimensions as dimensionsApi, investigations, metrics as metricsApi } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Statement } from "@/components/analysis/statement";
import { FilterChips } from "@/components/analysis/filter-chips";
import { ChangeValue } from "@/components/analysis/change";
import { StatusBadge } from "@/components/analysis/status";
import { Kpi } from "@/components/charts/kpi";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { InlineError } from "@/components/states/states";
import { SectionLabel } from "@/components/shell/page";
import { formatDateTime, formatShare, formatSignedChange, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ArtifactChart } from "./artifact-chart";
import { premiseVerdict, type NormNode } from "./model";

type Action = "drill" | "reject" | "annotate" | "modify" | null;

function useInvestigationMutation<TVars>(fn: (vars: TVars) => Promise<Investigation>, success?: string) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (inv) => {
      if (inv && typeof inv === "object" && "id" in inv) qc.setQueryData(qk.investigation(ws, inv.id), inv);
      void qc.invalidateQueries({ queryKey: ["ws", ws, "investigations"] });
      if (success) toast.success(success);
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

function DrillDialog({ open, onOpenChange, onDrill, pending, error, current }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onDrill: (dimension: string) => void;
  pending: boolean;
  error: unknown;
  current: string[];
}) {
  const { id: ws } = useWorkspace();
  const dims = useQuery({ queryKey: qk.dimensions(ws), queryFn: () => dimensionsApi.list(ws), enabled: open });
  const [dim, setDim] = React.useState("");
  const options = (dims.data ?? []).filter((d) => d.type !== "time" && !current.includes(d.name));
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (dim) onDrill(dim);
          }}
        >
          <DialogHeader>
            <DialogTitle>Branch into a dimension</DialogTitle>
            <DialogDescription>Runs a contribution analysis of this node&apos;s change across the chosen dimension, scoped to its segment.</DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-3">
            <Field label="Dimension" htmlFor="drill-dim" hint={dims.isLoading ? "Loading dimensions…" : undefined}>
              <NativeSelect id="drill-dim" value={dim} onChange={(e) => setDim(e.target.value)} autoFocus>
                <option value="">Choose a dimension…</option>
                {options.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.label || humanize(d.name)}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <InlineError error={dims.error ?? error} />
          </DialogBody>
          <DialogFooter>
            <Button onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button variant="primary" type="submit" disabled={!dim || pending}>
              <GitFork /> {pending ? "Running…" : "Branch"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function TextDialog({ open, onOpenChange, title, description, label, confirm, onSubmit, pending, error, required, destructive }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description: string;
  label: string;
  confirm: string;
  onSubmit: (text: string) => void;
  pending: boolean;
  error: unknown;
  required: boolean;
  destructive?: boolean;
}) {
  const [text, setText] = React.useState("");
  React.useEffect(() => {
    if (open) setText("");
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!required || text.trim()) onSubmit(text.trim());
          }}
        >
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-3">
            <Field label={label} htmlFor="node-text">
              <Textarea id="node-text" autoFocus value={text} onChange={(e) => setText(e.target.value)} rows={3} />
            </Field>
            <InlineError error={error} />
          </DialogBody>
          <DialogFooter>
            <Button onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button variant={destructive ? "danger" : "primary"} type="submit" disabled={pending || (required && !text.trim())}>
              {confirm}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ModifyDialog({ open, onOpenChange, node, onSubmit, pending, error }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  node: NormNode;
  onSubmit: (body: { metric_label?: string; dimension?: string; exclude?: { dimension: string; values: string[] } }) => void;
  pending: boolean;
  error: unknown;
}) {
  const { id: ws } = useWorkspace();
  const mets = useQuery({ queryKey: qk.metrics(ws), queryFn: () => metricsApi.list(ws), enabled: open });
  const dims = useQuery({ queryKey: qk.dimensions(ws), queryFn: () => dimensionsApi.list(ws), enabled: open });
  const [metric, setMetric] = React.useState(node.metric_id ?? "");
  const [dimension, setDimension] = React.useState(node.dimension ?? "");
  const [exDim, setExDim] = React.useState("");
  const [exVals, setExVals] = React.useState("");
  React.useEffect(() => {
    if (open) {
      setMetric(node.metric_id ?? "");
      setDimension(node.dimension ?? "");
      setExDim("");
      setExVals("");
    }
  }, [open, node]);
  const exValues = exVals.split(",").map((v) => v.trim()).filter(Boolean);
  const metricLabel = (mets.data ?? []).find((m) => m.name === metric);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit({
              metric_label: metric && metric !== node.metric_id ? metricLabel?.label || metric : undefined,
              dimension: dimension && dimension !== node.dimension ? dimension : undefined,
              exclude: exDim && exValues.length ? { dimension: exDim, values: exValues } : undefined,
            });
          }}
        >
          <DialogHeader>
            <DialogTitle>Modify analysis</DialogTitle>
            <DialogDescription>Change the metric, the breakdown dimension or exclude values. Each change is applied as a workspace command and recomputed from the data.</DialogDescription>
          </DialogHeader>
          <DialogBody className="grid gap-3 sm:grid-cols-2">
            <Field label="Metric" htmlFor="mod-metric">
              <NativeSelect id="mod-metric" value={metric} onChange={(e) => setMetric(e.target.value)}>
                <option value="">(unchanged)</option>
                {(mets.data ?? []).map((m) => (
                  <option key={m.id} value={m.name}>
                    {m.label || m.name}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Breakdown dimension" htmlFor="mod-dim">
              <NativeSelect id="mod-dim" value={dimension} onChange={(e) => setDimension(e.target.value)}>
                <option value="">(unchanged)</option>
                {(dims.data ?? []).filter((d) => d.type !== "time").map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.label || humanize(d.name)}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Exclude values of" htmlFor="mod-exdim">
              <NativeSelect id="mod-exdim" value={exDim} onChange={(e) => setExDim(e.target.value)}>
                <option value="">(no exclusion)</option>
                {(dims.data ?? []).filter((d) => d.type !== "time").map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.label || humanize(d.name)}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Values (comma separated)" htmlFor="mod-exvals">
              <Input id="mod-exvals" value={exVals} onChange={(e) => setExVals(e.target.value)} disabled={!exDim} placeholder="New stores, Online" />
            </Field>
            <InlineError error={mets.error ?? dims.error ?? error} className="sm:col-span-2" />
          </DialogBody>
          <DialogFooter>
            <Button onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button variant="primary" type="submit" disabled={pending || (!(exDim && exValues.length) && (!metric || metric === node.metric_id) && (!dimension || dimension === node.dimension))}>
              <Pencil /> {pending ? "Recomputing…" : "Apply and recompute"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Center panel: the selected node's statement, numbers, charts, filter context and actions. */
export function NodeDetail({
  investigation,
  node,
  artifacts,
  filters,
  expanded,
  onToggleExpanded,
  onExpandAll,
  canEdit,
  parentFormat = null,
}: {
  /** Number format of the parent node's metric: contribution effects are in the parent's units. */
  parentFormat?: string | null;
  investigation: Investigation;
  node: NormNode;
  artifacts: ArtifactRecord[];
  filters: FilterContextItem[];
  expanded: boolean;
  onToggleExpanded: () => void;
  onExpandAll: () => void;
  canEdit: boolean;
}) {
  const { id: ws, href } = useWorkspace();
  const [action, setAction] = React.useState<Action>(null);
  const invId = investigation.id;
  const close = () => setAction(null);

  const drill = useInvestigationMutation((dimension: string) => investigations.drill(ws, invId, node.id, dimension), "Branch added");
  const act = useInvestigationMutation(
    (body: Parameters<typeof investigations.nodeAction>[3]) => investigations.nodeAction(ws, invId, node.id, body),
  );
  const qc = useQueryClient();
  const router = useRouter();
  // Modifications go through the deterministic command parser so they are recorded exactly like typed commands.
  const modify = useMutation({
    mutationFn: async (body: { metric_label?: string; dimension?: string; exclude?: { dimension: string; values: string[] } }) => {
      const texts: string[] = [];
      if (body.metric_label) texts.push(`use ${body.metric_label} instead`);
      if (body.exclude) texts.push(`exclude ${body.exclude.values.join(", ")}`);
      if (body.dimension) texts.push(`break this down by ${body.dimension}`);
      // A metric switch or exclusion branches into a new investigation; later commands apply to that branch.
      let target = invId;
      let targetNode: string | null = node.id;
      let last: Awaited<ReturnType<typeof investigations.command>> | null = null;
      for (const text of texts) {
        last = await investigations.command(ws, target, { text, node_id: targetNode });
        if (last.action === "clarify" || last.command.unresolved?.length)
          throw new Error(last.command.unresolved?.length ? `${last.message} (${last.command.unresolved.join("; ")})` : last.message);
        if (last.action === "branched" && last.investigation_id) {
          target = last.investigation_id;
          targetNode = null;
        }
      }
      return { last, target };
    },
    onSuccess: ({ last, target }) => {
      void qc.invalidateQueries({ queryKey: qk.investigation(ws, invId) });
      toast.success(last?.message ?? "Analysis updated");
      if (target !== invId) router.push(href(`/investigate/${target}`));
    },
    onError: (e: Error) => toast.error("Could not modify the analysis", { description: e.message }),
  });
  const saveFinding = useMutation({
    mutationFn: () => investigations.saveFinding(ws, invId, node.id),
    onSuccess: (f) => {
      // Record the link immediately so the button flips to "Saved" before the refetch lands.
      qc.setQueryData<Investigation>(qk.investigation(ws, invId), (prev) =>
        prev ? { ...prev, node_findings: { ...(prev.node_findings ?? {}), [node.id]: f.id } } : prev,
      );
      void qc.invalidateQueries({ queryKey: ["ws", ws, "findings"] });
      void qc.invalidateQueries({ queryKey: qk.investigation(ws, invId) });
      toast.success("Saved as finding", {
        description: f.statement,
        action: { label: "Open", onClick: () => router.push(href(`/findings/${f.id}`)) },
      });
    },
    onError: (e: Error) => toast.error("Could not save finding", { description: e.message }),
  });

  // "F" saves the selected node as a finding when not typing.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "f" || e.metaKey || e.ctrlKey || e.altKey || !canEdit) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable || document.querySelector("[role=dialog]"))) return;
      e.preventDefault();
      if (!node.finding_id && !saveFinding.isPending) saveFinding.mutate();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [node.finding_id, saveFinding, canEdit]);

  const charts = artifacts.filter((a) => a.chart_spec || (a.kind === "chart" && a.result));
  const fallbackChart = !charts.length ? artifacts.find((a) => a.result && (a.result as { rows?: unknown[] }).rows?.length) : null;
  const fmt = node.format;
  const segPath = node.segment_path?.length ? node.segment_path : node.segment ? [node.segment] : [];
  const childDims = segPath.map((s) => s.dimension);

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-fg-subtle">
          <StatusBadge status={node.status} />
          {node.metric_label || node.metric_id ? <span>{node.metric_label ?? humanize(node.metric_id)}</span> : null}
          {segPath.length ? <span>· {segPath.map((s) => `${humanize(s.dimension)} = ${s.value ?? "(null)"}`).join(" › ")}</span> : null}
          {node.finding_id ? (
            <Link href={href(`/findings/${node.finding_id}`)} className="inline-flex items-center gap-1 text-accent hover:underline">
              <Lightbulb className="size-3" /> Saved finding
            </Link>
          ) : null}
        </div>
        <Statement type={node.statement_type} size="lg">
          {node.statement}
        </Statement>
        {premiseVerdict(node) ? (
          <p
            role="note"
            className={cn(
              "rounded border px-2 py-1 text-xs",
              premiseVerdict(node) === "holds"
                ? "border-positive/40 bg-positive-soft text-positive"
                : "border-negative/40 bg-negative-soft text-negative",
            )}
          >
            {premiseVerdict(node) === "holds"
              ? "The question's premise holds over these periods."
              : "The question's premise does not hold over these periods; read the rest of the tree with that in mind."}
          </p>
        ) : null}
        {node.decision_history?.length ? (
          <ul className="flex flex-col gap-0.5 text-xs text-fg-muted" aria-label="Decisions carried over">
            {node.decision_history.map((d, i) => (
              <li key={i}>
                Your earlier decision “{humanize(d.status ?? "")}” was carried over
                {d.carried_as && d.carried_as !== d.status ? ` as “${humanize(d.carried_as)}”` : ""}
                {d.changed?.length ? ` because ${d.changed.join(", ")} changed; please review` : ""}.
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      {node.current !== null && node.current !== undefined ? (
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-md border border-border px-4 py-3 sm:grid-cols-4">
          <Kpi label="Current" value={node.current} format={fmt} size="sm" />
          <Kpi label="Baseline" value={node.baseline} format={fmt} size="sm" />
          <div className="flex flex-col gap-0.5">
            <div className="text-xs text-fg-subtle">Change</div>
            <ChangeValue pct={node.pct_change} abs={node.abs_change} format={fmt} className="text-sm" />
          </div>
          <div className="flex flex-col gap-0.5">
            <div className="text-xs text-fg-subtle">Share of parent change</div>
            <div className="text-lg font-semibold tabular">{node.share !== null ? formatShare(node.share) : "n/a"}</div>
            {node.contribution && node.contribution.effect ? (
              <div className="text-2xs text-fg-subtle tabular">
                effect on parent {formatSignedChange(node.contribution.effect, parentFormat ?? fmt)}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      <div>
        <SectionLabel className="mb-1">Filter context</SectionLabel>
        <FilterChips items={filters} />
      </div>

      <div className="flex flex-wrap items-center gap-1.5 no-print" role="toolbar" aria-label="Node actions">
        <Button size="xs" onClick={onToggleExpanded} disabled={!node.children.length}>
          {expanded ? <ChevronsDownUp /> : <ChevronsUpDown />} {expanded ? "Collapse" : "Expand"}
        </Button>
        <Button size="xs" onClick={() => setAction("drill")} disabled={!canEdit}>
          <GitFork /> Branch / drill
        </Button>
        <Button size="xs" onClick={() => act.mutate({ action: "rerun" }, { onSuccess: () => toast.success("Node recomputed") })} disabled={!canEdit || act.isPending}>
          <RotateCw /> Rerun
        </Button>
        <Button size="xs" onClick={() => setAction("annotate")} disabled={!canEdit}>
          <MessageSquarePlus /> Annotate
        </Button>
        <Button
          size="xs"
          variant="primary"
          onClick={() => saveFinding.mutate()}
          disabled={!canEdit || saveFinding.isPending || !!node.finding_id}
          title="Save as finding (F)"
        >
          <Lightbulb /> {node.finding_id ? "Saved as finding" : "Save as finding"}
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="icon-xs" variant="ghost" aria-label="More node actions" disabled={!canEdit}>
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => act.mutate({ action: "confirm" }, { onSuccess: () => toast.success("Node confirmed") })}>
              <Check /> Confirm
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => act.mutate({ action: "needs_review" }, { onSuccess: () => toast.success("Marked for review") })}>
              <Flag /> Needs review
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => setAction("modify")}>
              <Pencil /> Modify metric, dimension or filters
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={onExpandAll}>
              <ChevronsUpDown /> Expand all below
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem destructive onSelect={() => setAction("reject")}>
              <CircleSlash /> Reject
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {charts.length || fallbackChart ? (
        <div className="grid gap-4 xl:grid-cols-2">
          {(charts.length ? charts : [fallbackChart!]).map((a) => (
            <div key={a.id} className="min-w-0 rounded-md border border-border p-3">
              <ArtifactChart artifact={a} filters={filters} />
            </div>
          ))}
        </div>
      ) : artifacts.length === 0 && node.statement_type === "hypothesis" ? (
        <p className="rounded border border-dashed border-hypothesis px-3 py-2 text-sm text-fg-muted">
          No test has been executed for this hypothesis. Branch into a dimension or add a plan step to test it.
        </p>
      ) : null}

      {node.annotations.length ? (
        <div>
          <SectionLabel className="mb-1">Annotations</SectionLabel>
          <ul className="flex flex-col gap-1.5">
            {node.annotations.map((a, i) => (
              <li key={a.id ?? i} className="rounded border border-border bg-bg-subtle px-2.5 py-1.5 text-sm">
                <p className="whitespace-pre-wrap">{a.text}</p>
                {a.author || a.created_at ? (
                  <p className="mt-0.5 text-2xs text-fg-subtle">
                    {a.author ?? ""} {a.created_at ? formatDateTime(a.created_at) : ""}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <DrillDialog
        open={action === "drill"}
        onOpenChange={(o) => !o && close()}
        onDrill={(d) => drill.mutate(d, { onSuccess: close })}
        pending={drill.isPending}
        error={drill.error}
        current={childDims}
      />
      <TextDialog
        open={action === "reject"}
        onOpenChange={(o) => !o && close()}
        title="Reject this node"
        description="Rejected nodes stay visible (struck through) so the reasoning trail is preserved."
        label="Reason"
        confirm="Reject"
        destructive
        required={false}
        onSubmit={(note) => act.mutate({ action: "reject", note: note || undefined }, { onSuccess: () => { close(); toast.success("Node rejected"); } })}
        pending={act.isPending}
        error={act.error}
      />
      <TextDialog
        open={action === "annotate"}
        onOpenChange={(o) => !o && close()}
        title="Annotate"
        description="Notes are stored with the node and carried into findings and reports."
        label="Note"
        confirm="Add note"
        required
        onSubmit={(note) => act.mutate({ action: "annotate", note }, { onSuccess: close })}
        pending={act.isPending}
        error={act.error}
      />
      <ModifyDialog
        open={action === "modify"}
        onOpenChange={(o) => !o && close()}
        node={node}
        onSubmit={(body) => modify.mutate(body, { onSuccess: close })}
        pending={modify.isPending}
        error={modify.error}
      />
    </div>
  );
}
