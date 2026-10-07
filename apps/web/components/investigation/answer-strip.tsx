"use client";
import * as React from "react";
import { CheckCircle2, AlertTriangle, XCircle, Scale } from "lucide-react";
import type { Bridge, Interpretation, Reconciliation } from "@/lib/api/types";
import { ChangeValue } from "@/components/analysis/change";
import { formatPctChange, formatSignedChange, formatValue } from "@/lib/format";
import { cn } from "@/lib/utils";
import { premiseVerdict, type NormNode, type NormTree } from "./model";
import { premiseNodes, reconciliationNode, valueLabels } from "./answer";
import { BridgeChart } from "./bridge-chart";

function Card({
  label,
  badge,
  children,
  onClick,
  testId,
  ariaLabel,
}: {
  label: string;
  badge?: React.ReactNode;
  children: React.ReactNode;
  onClick?: () => void;
  testId: string;
  ariaLabel: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      data-testid={testId}
      className="flex min-w-0 flex-col gap-1 rounded border border-border bg-bg px-3 py-2 text-left hover:border-border-strong hover:bg-bg-subtle focus-visible:outline-2 focus-visible:outline-ring"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-2xs font-semibold tracking-wider text-fg-subtle uppercase">{label}</span>
        {badge}
      </div>
      {children}
    </button>
  );
}

function Values({ node }: { node: NormNode }) {
  const l = valueLabels(node);
  const fmt = node.format;
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-xs tabular">
      <span>
        <span className="text-fg-subtle">{l.current} </span>
        <span className="text-sm font-semibold">{formatValue(node.current, fmt, { compact: true })}</span>
      </span>
      <span>
        <span className="text-fg-subtle">{l.baseline} </span>
        <span className="font-medium">{formatValue(node.baseline, fmt, { compact: true })}</span>
      </span>
      <span>
        <span className="text-fg-subtle">{l.change} </span>
        <ChangeValue pct={node.pct_change} abs={node.abs_change} format={fmt} showIcon={false} className="text-xs" />
      </span>
    </div>
  );
}

/** The premise as the question stated it, when the interpretation recorded it; else the node's own label. */
function premiseText(node: NormNode, interp: Interpretation | null | undefined): string {
  const p = interp?.premises?.find((x) => x.metric_id && x.metric_id === node.metric_id);
  return p ? `“${p.text}”` : node.title ?? node.statement;
}

export function reconciliationSentence(r: Reconciliation): string | null {
  const top = r.identities[0];
  if (!top) return null;
  const fmt = r.metric_format ?? "currency";
  const parts = top.components.map((c) => `${c.label} ${formatSignedChange(c.amount, fmt)}`).join(" + ");
  return `${parts} = ${formatSignedChange(top.parent_amount, fmt)}`;
}

/**
 * What an executive reads first, above the workstation panels: each premise with its verdict, the
 * headline gap, and whether the explanation adds back to it. Every card selects its node, so the
 * evidence behind it is one click away. Renders nothing for an investigation with none of these.
 */
export function AnswerStrip({
  tree,
  interpretation,
  onSelect,
  bridge = null,
}: {
  tree: NormTree;
  interpretation?: Interpretation | null;
  onSelect: (id: string) => void;
  /** The plan-to-actual bridge: when present it leads, and the cards stack beside it. */
  bridge?: Bridge | null;
}) {
  const premises = premiseNodes(tree).filter((n) => n.current !== null && n.current !== undefined);
  const recon = reconciliationNode(tree);
  const root = tree.rootId ? tree.nodes.get(tree.rootId) ?? null : null;
  if (!premises.length && !recon && !bridge) return null;
  // With a bridge, the chart carries plan, actual and every step, so the headline card is folded into
  // its title line and the premise and reconciliation cards sit beside it.
  const cards = <Cards premises={premises} recon={recon} root={bridge ? null : root} interpretation={interpretation} onSelect={onSelect} />;
  if (bridge)
    return (
      <div className="mt-2 grid gap-2 lg:grid-cols-[minmax(0,1fr)_minmax(300px,380px)]" role="group" aria-label="Answer summary">
        <section className="min-w-0 rounded border border-border bg-bg px-3 pt-2 pb-1" aria-label="Bridge from plan to actual" data-testid="bridge-card">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-2xs font-semibold tracking-wider text-fg-subtle uppercase">
                {bridge.metric_label}: {bridge.start_label.toLowerCase()} to {bridge.end_label.toLowerCase()}
              </span>
              {root && root.abs_change !== null && root.abs_change !== undefined ? (
                <button type="button" onClick={() => onSelect(root.id)} className="text-sm font-semibold hover:underline" data-testid="bridge-gap">
                  <span className={(root.abs_change ?? 0) < 0 ? "text-negative" : "text-positive"}>{formatSignedChange(root.abs_change, root.format)}</span>{" "}
                  <span className="text-xs font-normal text-fg-subtle">({formatPctChange(root.pct_change)} against {bridge.start_label.toLowerCase()})</span>
                </button>
              ) : null}
            </span>
            <span className="text-xs text-fg-subtle">Every bar is a reconciled node; click one for its evidence</span>
          </div>
          <BridgeChart bridge={bridge} onSelect={onSelect} height={206} />
        </section>
        <div className="grid content-start gap-2">{cards}</div>
      </div>
    );
  return (
    <div className="mt-2 grid gap-2 md:grid-cols-2 xl:grid-cols-3" role="group" aria-label="Answer summary">
      {cards}
    </div>
  );
}

function Cards({
  premises,
  recon,
  root,
  interpretation,
  onSelect,
}: {
  premises: NormNode[];
  recon: NormNode | null;
  root: NormNode | null;
  interpretation?: Interpretation | null;
  onSelect: (id: string) => void;
}) {
  return (
    <>
      {premises.map((p) => {
        const verdict = premiseVerdict(p);
        return (
          <Card
            key={p.id}
            testId="premise-card"
            label="Premise"
            ariaLabel={`Premise ${premiseText(p, interpretation)}: ${verdict === "holds" ? "supported" : verdict === "contradicted" ? "contradicted" : "checked"}`}
            onClick={() => onSelect(p.id)}
            badge={
              verdict === "holds" ? (
                <span className="inline-flex items-center gap-1 text-xs font-medium text-positive">
                  <CheckCircle2 className="size-3.5" aria-hidden /> Supported by the data
                </span>
              ) : verdict === "contradicted" ? (
                <span className="inline-flex items-center gap-1 text-xs font-medium text-negative">
                  <XCircle className="size-3.5" aria-hidden /> Contradicted by the data
                </span>
              ) : null
            }
          >
            <div className="truncate text-sm">
              {premiseText(p, interpretation)} <span className="text-fg-subtle">· {p.metric_label ?? p.metric_id}</span>
            </div>
            <Values node={p} />
          </Card>
        );
      })}
      {root && root.current !== null && root.current !== undefined ? (
        <Card
          testId="headline-card"
          label={`${root.metric_label ?? root.metric_id ?? "Headline"} · ${valueLabels(root).current.toLowerCase()} vs ${valueLabels(root).baseline.toLowerCase()}`}
          ariaLabel={`Headline: ${root.statement}`}
          onClick={() => onSelect(root.id)}
        >
          <div className={cn("text-xl font-semibold tracking-tight tabular", (root.abs_change ?? 0) < 0 ? "text-negative" : "text-fg")}>
            {formatSignedChange(root.abs_change, root.format)}
          </div>
          <Values node={root} />
        </Card>
      ) : null}
      {recon?.reconciliation ? (
        <Card
          testId="reconciliation-card"
          label="Reconciliation"
          ariaLabel={`Reconciliation: ${recon.reconciliation.reconciled ? "reconciles" : "does not reconcile"}`}
          onClick={() => onSelect(recon.id)}
          badge={
            recon.reconciliation.reconciled ? (
              <span className="inline-flex items-center gap-1 text-xs font-medium text-positive">
                <Scale className="size-3.5" aria-hidden /> Adds back to the gap
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-xs font-medium text-warning">
                <AlertTriangle className="size-3.5" aria-hidden /> Does not add back
              </span>
            )
          }
        >
          <div className="text-xs tabular text-fg-muted">{reconciliationSentence(recon.reconciliation)}</div>
          <div className="text-2xs text-fg-subtle tabular">
            Largest residual{" "}
            {formatValue(Math.max(0, ...recon.reconciliation.identities.map((i) => Math.abs(i.residual))), recon.reconciliation.metric_format ?? "currency")}{" "}
            across {recon.reconciliation.identities.length} {recon.reconciliation.identities.length === 1 ? "identity" : "identities"}
          </div>
        </Card>
      ) : null}
    </>
  );
}
