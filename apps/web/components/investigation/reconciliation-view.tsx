"use client";
import * as React from "react";
import { CheckCircle2, AlertTriangle } from "lucide-react";
import type { Reconciliation } from "@/lib/api/types";
import { Badge } from "@/components/ui/badge";
import { formatSignedChange, formatValue } from "@/lib/format";

/**
 * The reconciliation check: each identity the engine verified on executed numbers, written as
 * "parent = component + component (+ residual)". It answers "does the explanation add back to the gap?"
 */
export function ReconciliationView({ reconciliation: r, onSelect }: { reconciliation: Reconciliation; onSelect?: (nodeId: string) => void }) {
  const fmt = r.metric_format ?? "currency";
  return (
    <div className="flex flex-col gap-2" data-testid="reconciliation">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {r.reconciled ? (
          <Badge tone="positive">
            <CheckCircle2 /> Reconciles
          </Badge>
        ) : (
          <Badge tone="warning">
            <AlertTriangle /> Does not reconcile
          </Badge>
        )}
        <span className="text-fg-subtle tabular">
          {r.metric_label}: {formatValue(r.actual, fmt, { compact: true })} against {formatValue(r.baseline, fmt, { compact: true })}, gap{" "}
          {formatSignedChange(r.gap, fmt)}; tolerance {formatValue(r.tolerance, fmt)}
        </span>
      </div>
      <table className="w-full text-xs" aria-label="Reconciliation identities">
        <thead className="text-left text-2xs text-fg-subtle">
          <tr>
            <th className="py-1 pr-2 font-medium">Identity</th>
            <th className="py-1 pr-2 text-right font-medium">Parent</th>
            <th className="py-1 pr-2 text-right font-medium">Sum of parts</th>
            <th className="py-1 text-right font-medium">Residual</th>
          </tr>
        </thead>
        <tbody>
          {r.identities.map((i, k) => (
            <tr key={k} className="border-t border-border align-top tabular">
              <td className="py-1 pr-2">
                <span className="font-medium">{i.parent_label}</span>
                <span className="text-fg-subtle"> = </span>
                {i.components.map((c, j) => (
                  <React.Fragment key={j}>
                    {j > 0 ? <span className="text-fg-subtle"> + </span> : null}
                    {c.node_id && onSelect ? (
                      <button type="button" className="text-accent hover:underline" onClick={() => onSelect(c.node_id!)}>
                        {c.label}
                      </button>
                    ) : (
                      <span>{c.label}</span>
                    )}{" "}
                    <span className="text-fg-muted">{formatSignedChange(c.amount, fmt)}</span>
                  </React.Fragment>
                ))}
              </td>
              <td className="py-1 pr-2 text-right">{formatSignedChange(i.parent_amount, fmt)}</td>
              <td className="py-1 pr-2 text-right">{formatSignedChange(i.sum_of_components, fmt)}</td>
              <td className={i.reconciled ? "py-1 text-right text-positive" : "py-1 text-right text-warning"}>{formatValue(i.residual, fmt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
