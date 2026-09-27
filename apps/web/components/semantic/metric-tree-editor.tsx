"use client";
import * as React from "react";
import Link from "next/link";
import { Check, Plus, Trash2, X, CornerDownRight } from "lucide-react";
import type { DriverEdge, DriverRelation } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/input";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { RELATIONS, buildTreeRows, edgeKey, isSuggestedEdge, orphanEdges, relationMeta } from "./metric-utils";

export interface TreeMetricOption {
  id: string;
  label: string;
}

function RelationTag({ relation }: { relation: string }) {
  const meta = relationMeta(relation);
  return (
    <Tooltip content={meta.description}>
      <span
        className="inline-flex h-[18px] shrink-0 items-center rounded-sm border border-border-strong bg-bg-subtle px-1 font-mono text-2xs text-fg-muted"
        aria-label={`Relation: ${meta.label}`}
      >
        {meta.symbol}
      </span>
    </Tooltip>
  );
}

/**
 * Driver tree editor (spec §20–21). Approved edges are solid and canonical; suggested edges are dashed,
 * labeled "Suggested" and excluded from investigations until an analyst approves them.
 */
export function MetricTreeEditor({
  root,
  edges,
  metrics,
  onRemove,
  onDecide,
  onAdd,
  metricHref,
  readOnly = false,
  pendingKey,
}: {
  root: string;
  edges: DriverEdge[];
  metrics: TreeMetricOption[];
  onRemove?: (edge: DriverEdge) => void;
  onDecide?: (edge: DriverEdge, decision: "approve" | "reject") => void;
  onAdd?: (edge: DriverEdge) => void;
  metricHref?: (id: string) => string;
  readOnly?: boolean;
  pendingKey?: string | null;
}) {
  const label = React.useCallback((id: string) => metrics.find((m) => m.id === id)?.label ?? id, [metrics]);
  const rows = React.useMemo(() => buildTreeRows(root, edges), [root, edges]);
  const orphans = React.useMemo(() => orphanEdges(root, edges), [root, edges]);
  const inTree = [...new Set(rows.map((r) => r.metric))];

  const [parent, setParent] = React.useState(root);
  const [child, setChild] = React.useState("");
  const [relation, setRelation] = React.useState<DriverRelation>("multiplicative");
  React.useEffect(() => setParent(root), [root]);
  const duplicate = edges.some((e) => e.parent === parent && e.child === child);

  return (
    <div className="flex flex-col gap-3">
      <ul role="tree" aria-label={`Driver tree for ${label(root)}`} className="flex flex-col">
        {rows.map((r) => {
          const suggested = r.edge ? isSuggestedEdge(r.edge) : false;
          const key = r.path.join("/");
          const busy = r.edge && pendingKey === edgeKey(r.edge);
          return (
            <li
              key={key}
              role="treeitem"
              aria-level={r.depth + 1}
              aria-selected={false}
              data-suggested={r.edge ? String(suggested) : undefined}
              className="group flex min-h-8 items-center gap-2 border-b border-border/70 pr-1 last:border-b-0"
              style={{ paddingLeft: r.depth * 20 + 4 }}
            >
              {r.depth > 0 ? <CornerDownRight className="size-3.5 shrink-0 text-fg-faint" aria-hidden /> : null}
              {r.edge ? <RelationTag relation={r.edge.relation} /> : null}
              <span
                className={cn(
                  "inline-flex min-w-0 items-center gap-1.5 rounded px-1.5 py-0.5",
                  r.depth === 0 && "font-semibold",
                  suggested ? "border border-dashed border-hypothesis bg-hypothesis-soft" : "border border-transparent",
                )}
              >
                {metricHref ? (
                  <Link href={metricHref(r.metric)} className="truncate hover:underline">
                    {label(r.metric)}
                  </Link>
                ) : (
                  <span className="truncate">{label(r.metric)}</span>
                )}
                <span className="font-mono text-2xs text-fg-subtle">{r.metric}</span>
              </span>
              {suggested ? (
                <span className="text-2xs font-medium text-hypothesis" title="Not canonical until approved">
                  Suggested
                </span>
              ) : null}
              {r.cycle ? <span className="text-2xs text-negative">cycle</span> : null}
              {r.edge?.notes ? <span className="hidden truncate text-xs text-fg-subtle lg:inline">{r.edge.notes}</span> : null}
              {r.edge && !readOnly ? (
                <span className="ml-auto flex shrink-0 items-center gap-0.5">
                  {suggested && onDecide ? (
                    <>
                      <Button
                        size="xs"
                        variant="ghost"
                        disabled={!!busy}
                        onClick={() => onDecide(r.edge!, "approve")}
                        aria-label={`Approve ${label(r.edge.parent)} to ${label(r.edge.child)}`}
                      >
                        <Check className="text-positive" /> Approve
                      </Button>
                      <Button
                        size="xs"
                        variant="ghost"
                        disabled={!!busy}
                        onClick={() => onDecide(r.edge!, "reject")}
                        aria-label={`Reject ${label(r.edge.parent)} to ${label(r.edge.child)}`}
                      >
                        <X className="text-negative" /> Reject
                      </Button>
                    </>
                  ) : null}
                  {!suggested && onRemove ? (
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      className="opacity-60 group-hover:opacity-100 focus-visible:opacity-100"
                      onClick={() => onRemove(r.edge!)}
                      aria-label={`Remove edge ${label(r.edge.parent)} to ${label(r.edge.child)}`}
                    >
                      <Trash2 />
                    </Button>
                  ) : null}
                </span>
              ) : null}
            </li>
          );
        })}
      </ul>

      {orphans.length ? (
        <div className="rounded border border-warning/30 bg-warning-soft px-2.5 py-1.5 text-xs text-warning">
          {orphans.length} edge{orphans.length > 1 ? "s are" : " is"} not connected to {label(root)}:{" "}
          {orphans.map((o) => `${o.parent} → ${o.child}`).join(", ")}
        </div>
      ) : null}

      {!readOnly && onAdd ? (
        <form
          className="flex flex-wrap items-end gap-2 rounded border border-border bg-bg-subtle p-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!child || duplicate || child === parent) return;
            onAdd({ parent, child, relation, approved: true, suggested: false });
            setChild("");
          }}
          aria-label="Add driver edge"
        >
          <label className="flex flex-col gap-1 text-xs text-fg-muted">
            Parent
            <NativeSelect value={parent} onChange={(e) => setParent(e.target.value)} className="w-44">
              {inTree.map((m) => (
                <option key={m} value={m}>
                  {label(m)}
                </option>
              ))}
            </NativeSelect>
          </label>
          <label className="flex flex-col gap-1 text-xs text-fg-muted">
            Relation
            <NativeSelect value={relation} onChange={(e) => setRelation(e.target.value as DriverRelation)} className="w-44">
              {RELATIONS.map((r) => (
                <option key={r} value={r}>
                  {relationMeta(r).label}
                </option>
              ))}
            </NativeSelect>
          </label>
          <label className="flex flex-col gap-1 text-xs text-fg-muted">
            Driver (child)
            <NativeSelect value={child} onChange={(e) => setChild(e.target.value)} className="w-44">
              <option value="">Choose a metric…</option>
              {metrics
                .filter((m) => m.id !== parent)
                .map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
            </NativeSelect>
          </label>
          <Button type="submit" size="sm" disabled={!child || duplicate}>
            <Plus /> Add driver
          </Button>
          {duplicate ? <span className="text-xs text-fg-subtle">That edge already exists.</span> : null}
        </form>
      ) : null}
    </div>
  );
}
