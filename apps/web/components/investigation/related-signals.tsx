"use client";
import * as React from "react";
import { Unlink } from "lucide-react";
import { ChangeValue } from "@/components/analysis/change";
import { cn } from "@/lib/utils";
import type { RelatedSignal } from "@/lib/api/types";
import type { NormNode, NormTree } from "./model";
import { relatedSignalNodes, relationshipLabel, signalRecord } from "./answer";

/**
 * Related signals, listed apart from the explanation tree: verified observations in the segments
 * where the explanation concentrates, whose relationship to the explained change the data does not
 * establish. They are never part of the reconciliation; each says so in words.
 */
export function RelatedSignalsList({
  tree,
  selectedId,
  onSelect,
  signals = [],
}: {
  /** Investigation.related_signals: carries `provisional` for each signal node. */
  signals?: RelatedSignal[];
  tree: NormTree;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const nodes = relatedSignalNodes(tree);
  if (!nodes.length) return null;
  const root = tree.rootId ? tree.nodes.get(tree.rootId) : null;
  const relationship = relationshipLabel(root);
  return (
    <section aria-labelledby="related-signals-title" className="border-t border-border px-1 pt-2 pb-1" data-testid="related-signals">
      <h3 id="related-signals-title" className="flex items-center gap-1.5 px-2 text-2xs font-semibold tracking-wider text-fg-subtle uppercase">
        <Unlink className="size-3" aria-hidden /> Related signals
      </h3>
      <p className="px-2 pt-0.5 pb-1 text-2xs text-fg-subtle">Verified in the data, not part of the explanation above.</p>
      <ul className="flex flex-col">
        {nodes.map((n) => (
          <React.Fragment key={n.id}>
            <SignalRow
              node={n}
              relationship={relationship}
              provisional={!!signalRecord(signals, n.id)?.provisional}
              selected={selectedId === n.id}
              onSelect={() => onSelect(n.id)}
            />
            {n.children
              .map((c) => tree.nodes.get(c))
              .filter((c): c is NormNode => !!c)
              .map((c) => (
                <SupportingRow key={c.id} node={c} selected={selectedId === c.id} onSelect={() => onSelect(c.id)} />
              ))}
          </React.Fragment>
        ))}
      </ul>
    </section>
  );
}

/** A metric measured in the same segment as the signal (e.g. response time next to conversion). */
function SupportingRow({ node, selected, onSelect }: { node: NormNode; selected: boolean; onSelect: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-label={`Supporting measurement: ${node.statement}`}
        aria-current={selected ? "true" : undefined}
        data-node-id={node.id}
        className={cn(
          "ml-4 flex w-[calc(100%-1rem)] flex-col gap-0.5 rounded-sm border-l border-border px-2 py-1 text-left",
          selected ? "bg-accent-soft" : "hover:bg-bg-muted",
          "focus-visible:shadow-[inset_0_0_0_1px_var(--ring)] focus-visible:outline-none",
        )}
      >
        <span className="text-xs">{node.title ?? node.statement}</span>
        {node.title ? <span className="line-clamp-2 text-2xs text-fg-subtle">{node.statement}</span> : null}
      </button>
    </li>
  );
}

function SignalRow({
  node,
  relationship,
  provisional,
  selected,
  onSelect,
}: {
  node: NormNode;
  relationship: string;
  provisional: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-label={`Related signal: ${node.statement}. ${relationship}`}
        aria-current={selected ? "true" : undefined}
        data-node-id={node.id}
        className={cn(
          "flex w-full flex-col gap-0.5 rounded-sm px-2 py-1.5 text-left",
          selected ? "bg-accent-soft" : "hover:bg-bg-muted",
          "focus-visible:shadow-[inset_0_0_0_1px_var(--ring)] focus-visible:outline-none",
        )}
      >
        <span className="text-sm leading-[18px]">{node.title ?? node.statement}</span>
        {node.title ? <span className="line-clamp-2 text-2xs text-fg-subtle">{node.statement}</span> : null}
        <span className="flex flex-wrap items-center gap-x-2 text-2xs">
          {node.pct_change !== null && node.pct_change !== undefined ? (
            <ChangeValue pct={node.pct_change} abs={node.abs_change} format={node.format} className="text-2xs" showIcon={false} />
          ) : null}
          <span className="font-medium text-fg-muted">{relationship}</span>
          {provisional ? <span className="text-warning" title="Recent cohorts are still maturing">provisional</span> : null}
        </span>
      </button>
    </li>
  );
}
