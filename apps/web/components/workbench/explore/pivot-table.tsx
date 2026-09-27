"use client";
import * as React from "react";
import type { PivotResult, PivotValue } from "@/lib/pivot";
import { pivotValueLabel } from "@/lib/pivot";
import { formatValue } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Groups consecutive column keys sharing a prefix at `level` for multi-level headers. */
export function headerSpans(colKeys: string[][], level: number): { label: string; span: number }[] {
  const out: { label: string; span: number; prefix: string }[] = [];
  for (const k of colKeys) {
    const prefix = k.slice(0, level + 1).join("␟");
    const last = out[out.length - 1];
    if (last && last.prefix === prefix) last.span += 1;
    else out.push({ label: k[level], span: 1, prefix });
  }
  return out.map(({ label, span }) => ({ label, span }));
}

/**
 * Dense pivot rendering: sticky headers, one column per row dimension, multi-level column headers,
 * styled subtotal and grand-total rows. Null cells render blank with an explanatory title.
 */
export function PivotTable({
  pivot,
  rowFields,
  colFields,
  values,
  className,
}: {
  pivot: PivotResult;
  rowFields: string[];
  colFields: string[];
  values: PivotValue[];
  className?: string;
}) {
  const nV = values.length;
  const levels = colFields.length;
  const headerRowCount = levels + (nV > 1 || levels === 0 ? 1 : 0);
  const rowDimCols = Math.max(rowFields.length, 1);
  const colGroups = [...pivot.colKeys.map((k) => ({ key: k, total: false })), ...(pivot.hasRowTotalColumn && levels ? [{ key: [] as string[], total: true }] : [])];

  const valueLabel = (v: PivotValue) => pivotValueLabel(v);
  const cellTitle = (v: number | null) =>
    v === null ? (pivot.nonAdditiveNote ? "Not computed: distinct counts are not additive across groups" : "No data") : undefined;

  const thBase = "sticky z-10 border-b border-r border-border bg-bg-subtle px-2 py-1 text-left text-xs font-medium whitespace-nowrap";

  return (
    <div className={cn("overflow-auto scrollbar-thin", className)}>
      <table className="border-separate border-spacing-0 text-xs tabular" aria-label="Pivot table">
        <thead>
          {Array.from({ length: headerRowCount }).map((_, hr) => {
            const isValueRow = hr === levels;
            return (
              <tr key={hr}>
                {hr === 0
                  ? (rowFields.length ? rowFields : ["" ]).map((f, i) => (
                      <th
                        key={`rf-${i}`}
                        scope="col"
                        rowSpan={headerRowCount}
                        className={cn(thBase, "left-0 z-20")}
                        style={{ top: 0 }}
                      >
                        {f}
                      </th>
                    ))
                  : null}
                {isValueRow
                  ? colGroups.flatMap((g, gi) =>
                      values.map((v, vi) => (
                        <th key={`v-${gi}-${vi}`} scope="col" className={cn(thBase, "text-right", g.total && "bg-bg-muted")} style={{ top: hr * 26 }}>
                          {levels === 0 ? valueLabel(v) : nV > 1 ? valueLabel(v) : ""}
                        </th>
                      )),
                    )
                  : [
                      ...headerSpans(pivot.colKeys, hr).map((s, si) => (
                        <th
                          key={`c-${hr}-${si}`}
                          scope="colgroup"
                          colSpan={s.span * nV}
                          className={cn(thBase, "text-center")}
                          style={{ top: hr * 26 }}
                        >
                          {hr === levels - 1 && nV === 1 ? (
                            <span>
                              {s.label}
                              <span className="sr-only"> · {valueLabel(values[0])}</span>
                            </span>
                          ) : (
                            s.label
                          )}
                        </th>
                      )),
                      ...(hr === 0 && pivot.hasRowTotalColumn && levels
                        ? [
                            <th
                              key="total-h"
                              scope="colgroup"
                              colSpan={nV}
                              rowSpan={levels}
                              className={cn(thBase, "bg-bg-muted text-center")}
                              style={{ top: 0 }}
                            >
                              Total
                            </th>,
                          ]
                        : []),
                    ]}
              </tr>
            );
          })}
        </thead>
        <tbody>
          {pivot.rowHeaders.map((rh, ri) => {
            const prev = ri > 0 ? pivot.rowHeaders[ri - 1] : null;
            const rowCls =
              rh.kind === "grand"
                ? "bg-bg-muted font-semibold"
                : rh.kind === "subtotal"
                  ? "bg-bg-subtle font-medium"
                  : "hover:bg-bg-subtle";
            const headerCells =
              rh.kind === "grand" ? (
                <th scope="row" colSpan={rowDimCols} className="sticky left-0 border-r border-b border-border bg-inherit px-2 py-1 text-left">
                  Grand total
                </th>
              ) : rh.kind === "subtotal" ? (
                <>
                  {Array.from({ length: rh.depth }).map((_, d) => (
                    <td key={d} className="border-r border-b border-border px-2 py-1" />
                  ))}
                  <th scope="row" colSpan={rowDimCols - rh.depth} className="border-r border-b border-border px-2 py-1 text-left">
                    {rh.label}
                  </th>
                </>
              ) : (
                (rowFields.length ? rowFields : [""]).map((_, d) => {
                  const same =
                    prev && prev.kind === "leaf" && rh.key.slice(0, d + 1).every((v, i) => prev.key[i] === v) && d < rowFields.length - 1;
                  return (
                    <th key={d} scope="row" className="border-r border-b border-border px-2 py-1 text-left font-normal whitespace-nowrap">
                      {same ? "" : (rh.key[d] ?? "")}
                    </th>
                  );
                })
              );
            return (
              <tr key={ri} className={rowCls} data-row-kind={rh.kind}>
                {headerCells}
                {pivot.values[ri].map((cell, ci) =>
                  cell.map((v, vi) => (
                    <td
                      key={`${ci}-${vi}`}
                      title={cellTitle(v)}
                      className={cn(
                        "border-r border-b border-border px-2 py-1 text-right font-mono whitespace-nowrap",
                        colGroups[ci]?.total && "bg-bg-subtle font-medium",
                      )}
                    >
                      {v === null ? "" : formatValue(v, values[vi].format ?? undefined)}
                    </td>
                  )),
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
      {pivot.nonAdditiveNote ? <p className="px-1 pt-2 text-xs text-fg-subtle">{pivot.nonAdditiveNote}</p> : null}
    </div>
  );
}
