"use client";
import * as React from "react";
import { Suspense, use } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowDown,
  ArrowUp,
  BookOpen,
  CheckCircle2,
  EyeOff,
  Lock,
  Pencil,
  Plus,
  Printer,
  Send,
  Trash2,
  Undo2,
} from "lucide-react";
import { reports, type ReportBlockSpec, type ReportOut } from "@/lib/api/resources/outputs";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader } from "@/components/shell/page";
import { ErrorState, LoadingState, EmptyState } from "@/components/states/states";
import { StatusBadge } from "@/components/analysis/status";
import { ExportMenu } from "@/components/export/export-menu";
import { uploadChartImages } from "@/components/charts/capture";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Segmented } from "@/components/ui/tabs";
import { Tooltip } from "@/components/ui/tooltip";
import { Spinner } from "@/components/ui/spinner";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { BlockEditor, BlockView } from "@/components/outputs/report-blocks";
import { ReviewPanel } from "@/components/outputs/review-panel";
import { BLOCK_LABELS, editBlock, monthLabel, moveBlock, newBlock, publishRefusal, reviewState, type PublishRefusal } from "@/components/outputs/model";
import { formatDateTime, formatRelative, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";

const ADDABLE = ["heading", "narrative", "finding", "kpi", "chart", "table", "summary", "methodology", "sources", "open_questions"];

function AddBlockMenu({ onAdd, label = "Add block", size = "sm" }: { onAdd: (type: string) => void; label?: string; size?: "xs" | "sm" }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" size={size}>
          <Plus /> {label}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        {ADDABLE.map((t) => (
          <DropdownMenuItem key={t} onSelect={() => onAdd(t)}>
            {BLOCK_LABELS[t]}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function BlockCard({
  block,
  index,
  count,
  allBlocks,
  reviewing,
  onChange,
  onMove,
  onRemove,
  onInsertBelow,
}: {
  block: ReportBlockSpec;
  index: number;
  count: number;
  allBlocks: ReportBlockSpec[];
  reviewing: boolean;
  onChange: (patch: Partial<ReportBlockSpec>) => void;
  onMove: (d: -1 | 1) => void;
  onRemove: () => void;
  onInsertBelow: (type: string) => void;
}) {
  const label = BLOCK_LABELS[block.type] ?? humanize(block.type);
  return (
    <article
      id={`block-${block.id}`}
      aria-label={`${label} block`}
      className={cn("group rounded-md border border-border bg-bg", block.excluded && "border-dashed opacity-60")}
    >
      <header className="flex flex-wrap items-center gap-1 border-b border-border px-2 py-1">
        <span className="text-2xs font-semibold tracking-wider text-fg-subtle uppercase">{label}</span>
        {block.excluded ? (
          <span className="inline-flex items-center gap-1 text-2xs text-fg-subtle">
            <EyeOff className="size-3" /> Excluded from publication
          </span>
        ) : block.reviewed ? (
          <span className="inline-flex items-center gap-1 text-2xs text-positive">
            <CheckCircle2 className="size-3" /> Reviewed
          </span>
        ) : reviewing ? (
          <span className="text-2xs text-warning">Needs review</span>
        ) : null}
        <div className="ml-auto flex items-center gap-0.5">
          {reviewing && !block.excluded ? (
            <Button size="xs" variant={block.reviewed ? "ghost" : "secondary"} onClick={() => onChange({ reviewed: !block.reviewed })}>
              <CheckCircle2 /> {block.reviewed ? "Unmark" : "Mark reviewed"}
            </Button>
          ) : null}
          <Button size="xs" variant="ghost" onClick={() => onChange({ excluded: !block.excluded, reviewed: false })}>
            <EyeOff /> {block.excluded ? "Include" : "Exclude"}
          </Button>
          <Button size="icon-xs" variant="ghost" disabled={index === 0} onClick={() => onMove(-1)} aria-label="Move block up">
            <ArrowUp />
          </Button>
          <Button size="icon-xs" variant="ghost" disabled={index === count - 1} onClick={() => onMove(1)} aria-label="Move block down">
            <ArrowDown />
          </Button>
          <Button size="icon-xs" variant="ghost" onClick={onRemove} aria-label="Delete block">
            <Trash2 />
          </Button>
        </div>
      </header>
      <div className="flex flex-col gap-3 p-3">
        <BlockEditor block={block} allBlocks={allBlocks} onChange={onChange} />
        {block.type !== "heading" ? (
          <div className="rounded border border-dashed border-border px-3 py-2">
            <div className="mb-1 text-2xs tracking-wider text-fg-faint uppercase">Preview</div>
            <BlockView block={block} />
          </div>
        ) : null}
      </div>
      <div className="flex justify-center pb-2 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
        <AddBlockMenu onAdd={onInsertBelow} label="Insert below" size="xs" />
      </div>
    </article>
  );
}

function ReadingView({ report, blocks }: { report: ReportOut; blocks: ReportBlockSpec[] }) {
  const visible = blocks.filter((b) => !b.excluded);
  return (
    <article className="mx-auto flex max-w-3xl flex-col gap-5 rounded-md border border-border bg-bg px-6 py-6 print:max-w-none print:border-0 print:p-0" aria-label={report.title}>
      <header className="border-b border-border pb-3">
        <h1 className="text-xl font-semibold tracking-tight">{report.title}</h1>
        <p className="mt-1 text-xs text-fg-subtle">
          {humanize(report.kind)}
          {report.period ? ` · ${monthLabel(report.period)}` : ""} · version {report.version_no}
          {report.published_at ? ` · published ${formatDateTime(report.published_at)}` : ` · ${humanize(report.status)}`}
        </p>
      </header>
      {visible.length ? (
        visible.map((b) => (
          <section key={b.id} data-print-avoid-break className="relative">
            {report.status !== "published" && !b.reviewed ? (
              <span className="absolute -left-4 top-1 h-3 w-1 rounded-full bg-warning no-print" title="Not reviewed yet" aria-label="Not reviewed yet" />
            ) : null}
            <BlockView block={b} />
          </section>
        ))
      ) : (
        <p className="text-sm text-fg-subtle italic">This report has no included blocks.</p>
      )}
    </article>
  );
}

function ReportEditor({ id }: { id: string }) {
  const { id: ws, href, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const query = useQuery({ queryKey: qk.report(ws, id), queryFn: () => reports.get(ws, id) });
  const report = query.data;
  const [blocks, setBlocks] = React.useState<ReportBlockSpec[]>([]);
  const [title, setTitle] = React.useState("");
  const [mode, setMode] = React.useState<"edit" | "read">("read");
  const [dirty, setDirty] = React.useState(0);
  const [removing, setRemoving] = React.useState<string | null>(null);
  const hydrated = React.useRef<string | null>(null);

  React.useEffect(() => {
    if (!report) return;
    const key = `${report.id}:${report.status}:${report.version_no}`;
    if (hydrated.current === key) return;
    const first = hydrated.current === null;
    hydrated.current = key;
    setBlocks(report.blocks ?? []);
    setTitle(report.title);
    if (first) setMode(report.status === "published" || !canEdit ? "read" : "edit");
    if (report.status === "published") setMode("read");
  }, [report, canEdit]);

  const readOnly = !canEdit || report?.status === "published";

  const save = useMutation({
    mutationFn: (body: { title: string; blocks: ReportBlockSpec[] }) => reports.update(ws, id, body),
    onSuccess: (r) => {
      hydrated.current = `${r.id}:${r.status}:${r.version_no}`;
      qc.setQueryData(qk.report(ws, id), r);
      void qc.invalidateQueries({ queryKey: qk.reports(ws) });
    },
    onError: (e: Error) => toast.error("Report not saved", { description: e.message }),
  });

  // Debounced autosave of title + blocks.
  const latest = React.useRef({ title, blocks });
  latest.current = { title, blocks };
  React.useEffect(() => {
    if (!dirty || readOnly) return;
    const at = dirty;
    const t = setTimeout(
      () => save.mutate({ ...latest.current }, { onSuccess: () => setDirty((d) => (d === at ? 0 : d)) }),
      800,
    );
    return () => clearTimeout(t);
    // save.mutate is stable; `dirty` increments on every edit
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty, readOnly]);

  const flush = async () => {
    if (dirty && !readOnly) {
      await save.mutateAsync({ ...latest.current });
      setDirty(0);
    }
  };

  const [refusal, setRefusal] = React.useState<PublishRefusal | null>(null);
  const transition = useMutation({
    mutationFn: async (action: "submit" | "publish" | "unpublish") => {
      await flush();
      if (action === "submit") return reports.submitForReview(ws, id);
      if (action === "publish") return reports.publish(ws, id);
      return reports.unpublish(ws, id);
    },
    onSuccess: (r, action) => {
      setRefusal(null);
      qc.setQueryData(qk.report(ws, id), r);
      void qc.invalidateQueries({ queryKey: qk.reports(ws) });
      toast.success(action === "submit" ? "Submitted for review" : action === "publish" ? `Published as version ${r.version_no}` : "Unpublished; editing enabled");
    },
    onError: (e: Error, action) => {
      if (action !== "publish") {
        toast.error("Status change failed", { description: e.message });
        return;
      }
      const r = publishRefusal(e);
      setRefusal(r);
      // The server's copy is authoritative; reload it so the checklist matches what was refused.
      void query.refetch();
      toast.error("Not published", {
        description:
          r.kind === "unreviewed"
            ? `${r.blockIds.length} block${r.blockIds.length === 1 ? " is" : "s are"} not reviewed. Review or exclude ${r.blockIds.length === 1 ? "it" : "them"}, then publish.`
            : r.kind === "not_in_review"
              ? "Submit the report for review before publishing."
              : r.message,
      });
    },
  });

  const update = (next: ReportBlockSpec[]) => {
    setBlocks(next);
    setDirty((d) => d + 1);
  };
  const patchBlock = (bid: string, patch: Partial<ReportBlockSpec>) =>
    update(blocks.map((b) => (b.id === bid ? editBlock(b, patch) : b)));
  const insertAt = (index: number, type: string) => {
    const next = [...blocks];
    next.splice(index, 0, newBlock(type));
    update(next);
  };

  if (query.isLoading) return <LoadingState variant="block" label="Loading report" />;
  if (query.isError || !report) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;

  const state = reviewState({ status: report.status, blocks });
  const reviewing = report.status !== "published";
  const saving = save.isPending || (dirty > 0 && !readOnly);

  return (
    <Page>
      <PageHeader
        breadcrumb={
          <Link href={href("/reports")} className="hover:text-fg">
            Reports
          </Link>
        }
        title={
          mode === "edit" && !readOnly ? (
            <Input
              aria-label="Report title"
              value={title}
              onChange={(e) => {
                setTitle(e.target.value);
                setDirty((d) => d + 1);
              }}
              className="h-8 max-w-xl text-lg font-semibold"
            />
          ) : (
            report.title
          )
        }
        meta={
          <>
            <StatusBadge status={report.status} />
            <span>{humanize(report.kind)}</span>
            {report.period ? <span>{monthLabel(report.period)}</span> : null}
            <span className="font-mono">v{report.version_no}</span>
            <span>Updated {formatRelative(report.updated_at ?? report.created_at)}</span>
            {readOnly && report.status === "published" ? (
              <span className="inline-flex items-center gap-1">
                <Lock className="size-3" /> Published reports are read-only
              </span>
            ) : saving ? (
              <span className="inline-flex items-center gap-1">
                <Spinner /> Saving
              </span>
            ) : dirty === 0 && save.isSuccess ? (
              <span>Saved</span>
            ) : null}
          </>
        }
        actions={
          <div className="flex flex-wrap items-center gap-1.5 no-print">
            {!readOnly ? (
              <Segmented<"edit" | "read">
                aria-label="View mode"
                value={mode}
                onChange={setMode}
                options={[
                  { value: "edit", label: (<><Pencil /> Edit</>) },
                  { value: "read", label: (<><BookOpen /> Read</>) },
                ]}
              />
            ) : null}
            {canEdit && report.status === "draft" ? (
              <Button variant="secondary" disabled={!state.canSubmit || transition.isPending} onClick={() => transition.mutate("submit")}>
                <Send /> Submit for review
              </Button>
            ) : null}
            {canEdit && report.status !== "published" ? (
              <Tooltip content={state.canPublish ? "Publish this version" : state.blockers.join(" ")}>
                <span>
                  <Button variant="primary" disabled={!state.canPublish || transition.isPending} onClick={() => transition.mutate("publish")}>
                    <CheckCircle2 /> Publish
                  </Button>
                </span>
              </Tooltip>
            ) : null}
            {canEdit && report.status === "published" ? (
              <Button variant="secondary" disabled={transition.isPending} onClick={() => transition.mutate("unpublish")}>
                <Undo2 /> Unpublish to edit
              </Button>
            ) : null}
            <Button variant="secondary" onClick={() => { setMode("read"); setTimeout(() => window.print(), 80); }}>
              <Printer /> Print
            </Button>
            <ExportMenu
              target={{ kind: "report", id }}
              formats={["pdf", "html", "md"]}
              filename={report.title}
              beforeExport={async () => {
                const r = await uploadChartImages(ws, "report_block");
                if (r.failed) toast.warning(`${r.failed} chart image${r.failed === 1 ? "" : "s"} could not be attached`);
              }}
            />
          </div>
        }
      />
      <PageBody className="bg-bg-subtle print:overflow-visible print:bg-white">
        <div className={cn("grid gap-4", reviewing && canEdit && "lg:grid-cols-[minmax(0,1fr)_280px]")}>
          <div className="min-w-0">
            {refusal && report.status !== "published" ? (
              <div role="alert" className="mx-auto mb-3 max-w-3xl rounded-md border border-negative/40 bg-negative-soft px-3 py-2 text-sm no-print">
                <div className="font-medium text-negative">Publishing was refused by the server</div>
                {refusal.kind === "unreviewed" ? (
                  <>
                    <p className="text-fg-muted">Every included block must be reviewed (or excluded) before publication:</p>
                    <ul className="mt-1 flex flex-col gap-0.5">
                      {refusal.blockIds.map((bid) => {
                        const i = blocks.findIndex((b) => b.id === bid);
                        const b = blocks[i];
                        return (
                          <li key={bid}>
                            <button
                              type="button"
                              className="text-left text-accent hover:underline"
                              onClick={() => {
                                setMode("edit");
                                setTimeout(() => document.getElementById(`block-${bid}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
                              }}
                            >
                              {b ? `Block ${i + 1}: ${BLOCK_LABELS[b.type] ?? b.type}${b.title ? `: ${b.title}` : ""}` : `Block ${bid}`}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </>
                ) : (
                  <p className="text-fg-muted">{refusal.kind === "not_in_review" ? "Submit the report for review first." : refusal.message}</p>
                )}
              </div>
            ) : null}
            {mode === "read" || readOnly ? (
              <ReadingView report={report} blocks={blocks} />
            ) : blocks.length === 0 ? (
              <EmptyState
                title="This report is empty"
                description="Add a narrative, findings with their evidence, KPIs, charts and a methodology section."
                action={<AddBlockMenu onAdd={(t) => insertAt(0, t)} />}
              />
            ) : (
              <div className="mx-auto flex max-w-3xl flex-col gap-3">
                {blocks.map((b, i) => (
                  <BlockCard
                    key={b.id}
                    block={b}
                    index={i}
                    count={blocks.length}
                    allBlocks={blocks}
                    reviewing={reviewing && report.status === "in_review"}
                    onChange={(p) => patchBlock(b.id, p)}
                    onMove={(d) => update(moveBlock(blocks, b.id, d))}
                    onRemove={() => setRemoving(b.id)}
                    onInsertBelow={(t) => insertAt(i + 1, t)}
                  />
                ))}
                <div className="flex justify-center">
                  <AddBlockMenu onAdd={(t) => insertAt(blocks.length, t)} />
                </div>
              </div>
            )}
          </div>
          {reviewing && canEdit ? (
            <div className="lg:sticky lg:top-0 lg:self-start">
              <ReviewPanel
                report={report}
                blocks={blocks}
                readOnly={readOnly}
                onToggle={(bid, patch) => patchBlock(bid, patch)}
                refused={refusal?.kind === "unreviewed" ? refusal.blockIds : []}
                onFocusBlock={(bid) => {
                  setMode("edit");
                  setTimeout(() => document.getElementById(`block-${bid}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
                }}
              />
              {report.status === "draft" ? (
                <p className="mt-2 text-xs text-fg-subtle no-print">
                  Submit for review to start the checklist. Editing a reviewed block clears its review.
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      </PageBody>
      <ConfirmDialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title="Delete this block?"
        confirmLabel="Delete"
        destructive
        onConfirm={() => {
          if (removing) update(blocks.filter((b) => b.id !== removing));
          setRemoving(null);
        }}
      />
    </Page>
  );
}

export default function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <Suspense fallback={<LoadingState variant="block" />}>
      <ReportEditor id={id} />
    </Suspense>
  );
}
