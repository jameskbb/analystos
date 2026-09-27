"use client";
import * as React from "react";
import { ChevronRight, CircleAlert, Lightbulb } from "lucide-react";
import { normalizeStatementType } from "@/components/analysis/statement";
import { EVIDENCE_META, normalizeEvidence } from "@/components/analysis/evidence";
import { ChangeValue, ShareBar } from "@/components/analysis/change";
import { ancestors, premiseVerdict, visibleNodes, type NormNode, type NormTree } from "./model";
import { cn } from "@/lib/utils";

const TYPE_MARK: Record<string, { cls: string; label: string }> = {
  observation: { cls: "border-fg-faint bg-bg", label: "Observation" },
  supported_explanation: { cls: "border-accent bg-accent", label: "Supported explanation" },
  hypothesis: { cls: "border-hypothesis border-dashed bg-transparent", label: "Hypothesis" },
};

function TypeMark({ type }: { type: string }) {
  const t = normalizeStatementType(type);
  const m = TYPE_MARK[t];
  return (
    <span
      className={cn("mt-[5px] size-2.5 shrink-0 rounded-[2px] border-[1.5px]", m.cls)}
      title={m.label}
      aria-hidden
      data-mark={t}
    />
  );
}

function EvidenceDots({ strength }: { strength: string }) {
  const s = normalizeEvidence(strength);
  const meta = EVIDENCE_META[s];
  return (
    <span className="inline-flex items-center gap-[2px]" title={meta.label} aria-label={meta.label}>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-2 w-[3px] rounded-[1px]"
          style={{
            background: i < meta.bars ? meta.color : "transparent",
            border: i < meta.bars ? "none" : `1px ${s === "hypothesis_only" ? "dashed" : "solid"} ${meta.color}`,
          }}
        />
      ))}
    </span>
  );
}

function TreeRow({
  node,
  depth,
  expanded,
  selected,
  focused,
  onToggle,
  onSelect,
  rowRef,
}: {
  node: NormNode;
  depth: number;
  expanded: boolean;
  selected: boolean;
  focused: boolean;
  onToggle: () => void;
  onSelect: () => void;
  rowRef: (el: HTMLDivElement | null) => void;
}) {
  const t = normalizeStatementType(node.statement_type);
  const rejected = node.status === "rejected";
  const failed = node.status === "failed" || node.kind === "failed";
  const hasKids = node.children.length > 0;
  return (
    <div
      ref={rowRef}
      role="treeitem"
      aria-level={depth + 1}
      aria-selected={selected}
      aria-expanded={hasKids ? expanded : undefined}
      aria-label={`${TYPE_MARK[t].label}: ${node.statement}${rejected ? " (rejected)" : ""}`}
      tabIndex={focused ? 0 : -1}
      data-node-id={node.id}
      data-status={node.status}
      onClick={onSelect}
      className={cn(
        "group relative flex cursor-default items-start gap-1 rounded-sm py-1 pr-2 outline-none",
        selected ? "bg-accent-soft" : "hover:bg-bg-muted",
        "focus-visible:shadow-[inset_0_0_0_1px_var(--ring)]",
      )}
      style={{ paddingLeft: 4 + depth * 14 }}
    >
      {depth > 0 ? (
        <span aria-hidden className="absolute top-0 bottom-0 border-l border-border" style={{ left: depth * 14 - 4 }} />
      ) : null}
      <button
        type="button"
        tabIndex={-1}
        aria-hidden
        onClick={(e) => {
          e.stopPropagation();
          onToggle();
        }}
        className={cn(
          "mt-[1px] flex size-4 shrink-0 items-center justify-center rounded-sm text-fg-subtle hover:bg-bg-muted",
          !hasKids && "invisible",
        )}
      >
        <ChevronRight className={cn("size-3 transition-transform", expanded && "rotate-90")} />
      </button>
      <TypeMark type={t} />
      <div className={cn("min-w-0 flex-1", (rejected || failed) && "opacity-55")}>
        <div
          className={cn(
            "text-sm leading-[18px]",
            t === "hypothesis" && "text-fg-muted italic",
            rejected && "line-through decoration-fg-faint",
            depth === 0 && "font-medium",
          )}
        >
          {node.statement}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-fg-subtle">
          {node.pct_change !== null && node.pct_change !== undefined ? (
            <ChangeValue pct={node.pct_change} abs={node.abs_change} format={node.format} className="text-2xs" showIcon={false} />
          ) : node.abs_change !== null && node.abs_change !== undefined ? (
            <ChangeValue abs={node.abs_change} format={node.format} className="text-2xs" showIcon={false} />
          ) : null}
          {depth > 0 && node.share !== null ? <ShareBar share={node.share} /> : null}
          <EvidenceDots strength={node.evidence_strength} />
          {rejected ? <span className="text-negative">rejected</span> : null}
          {node.status === "confirmed" ? <span className="text-positive">confirmed</span> : null}
          {node.status === "needs_review" ? <span className="text-warning">needs review</span> : null}
          {failed ? (
            <span className="inline-flex items-center gap-0.5 text-negative">
              <CircleAlert className="size-3" /> failed
            </span>
          ) : null}
          {premiseVerdict(node) === "holds" ? <span className="text-positive">premise holds</span> : null}
          {premiseVerdict(node) === "contradicted" ? <span className="font-medium text-negative">premise contradicted</span> : null}
          {node.decision_history?.length ? <span title="Analyst decision carried over from an earlier run">carried over</span> : null}
          {node.finding_id ? (
            <span className="inline-flex items-center gap-0.5 text-accent">
              <Lightbulb className="size-3" /> finding
            </span>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/**
 * Investigation tree (spec §1, §17). WAI-ARIA tree with roving focus:
 * ↑/↓ move, → expand or enter, ← collapse or go to parent, Home/End, Enter selects.
 */
export function InvestigationTreeView({
  tree,
  selectedId,
  onSelect,
  expanded,
  onExpandedChange,
  className,
}: {
  tree: NormTree;
  selectedId: string | null;
  onSelect: (id: string) => void;
  expanded: Set<string>;
  onExpandedChange: (next: Set<string>) => void;
  className?: string;
}) {
  const rows = React.useMemo(() => visibleNodes(tree, expanded), [tree, expanded]);
  const [focusId, setFocusId] = React.useState<string | null>(selectedId ?? tree.rootId);
  const refs = React.useRef(new Map<string, HTMLDivElement>());

  // Keep the selected node visible when it changes from outside (command bar, diff links).
  React.useEffect(() => {
    if (!selectedId || !tree.nodes.has(selectedId)) return;
    setFocusId(selectedId);
    const anc = ancestors(tree, selectedId);
    if (anc.some((a) => !expanded.has(a))) {
      const next = new Set(expanded);
      anc.forEach((a) => next.add(a));
      onExpandedChange(next);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, tree]);

  const focusRow = (id: string) => {
    setFocusId(id);
    refs.current.get(id)?.focus();
  };

  const toggle = (id: string) => {
    const next = new Set(expanded);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onExpandedChange(next);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    const idx = rows.findIndex((r) => r.id === focusId);
    if (idx < 0) return;
    const node = tree.nodes.get(rows[idx].id)!;
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        if (idx < rows.length - 1) {
          focusRow(rows[idx + 1].id);
          onSelect(rows[idx + 1].id);
        }
        break;
      case "ArrowUp":
        e.preventDefault();
        if (idx > 0) {
          focusRow(rows[idx - 1].id);
          onSelect(rows[idx - 1].id);
        }
        break;
      case "ArrowRight":
        e.preventDefault();
        if (node.children.length && !expanded.has(node.id)) toggle(node.id);
        else if (node.children.length) {
          focusRow(node.children[0]);
          onSelect(node.children[0]);
        }
        break;
      case "ArrowLeft":
        e.preventDefault();
        if (node.children.length && expanded.has(node.id)) toggle(node.id);
        else if (node.parent_id && tree.nodes.has(node.parent_id)) {
          focusRow(node.parent_id);
          onSelect(node.parent_id);
        }
        break;
      case "Home":
        e.preventDefault();
        focusRow(rows[0].id);
        onSelect(rows[0].id);
        break;
      case "End":
        e.preventDefault();
        focusRow(rows[rows.length - 1].id);
        onSelect(rows[rows.length - 1].id);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        onSelect(node.id);
        break;
    }
  };

  if (!tree.rootId) return null;
  return (
    <div role="tree" aria-label="Investigation tree" onKeyDown={onKeyDown} className={cn("flex flex-col py-1", className)}>
      {rows.map((r) => {
        const n = tree.nodes.get(r.id)!;
        return (
          <TreeRow
            key={r.id}
            node={n}
            depth={r.depth}
            expanded={expanded.has(r.id)}
            selected={selectedId === r.id}
            focused={(focusId ?? tree.rootId) === r.id}
            onToggle={() => toggle(r.id)}
            onSelect={() => {
              setFocusId(r.id);
              onSelect(r.id);
            }}
            rowRef={(el) => {
              if (el) refs.current.set(r.id, el);
              else refs.current.delete(r.id);
            }}
          />
        );
      })}
    </div>
  );
}
