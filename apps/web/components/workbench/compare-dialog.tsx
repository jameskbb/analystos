"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { queries, type CompareResult } from "@/lib/api/resources/workbench";
import { useWorkspace } from "@/components/providers/workspace";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DataGrid } from "@/components/data-grid/data-grid";
import { ErrorState, LoadingState } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { formatInt } from "@/lib/format";

/** Turns compare rows (dicts) into a grid; `_status` first, then keys, then measure columns. */
export function compareToGrid(res: CompareResult): { columns: { name: string; type: string }[]; rows: unknown[][] } {
  const first = res.rows[0] ?? {};
  const all = Object.keys(first);
  const ordered = ["_status", ...res.keys, ...all.filter((k) => k !== "_status" && !res.keys.includes(k))].filter(
    (k, i, a) => a.indexOf(k) === i && (k in first || res.keys.includes(k)),
  );
  const columns = ordered.map((name) => ({
    name,
    type: typeof first[name] === "number" ? "DOUBLE" : "VARCHAR",
  }));
  return { columns, rows: res.rows.map((r) => ordered.map((k) => r[k] ?? null)) };
}

export function CompareRunsDialog({
  open,
  onOpenChange,
  leftId,
  rightId,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  leftId: string;
  rightId: string;
}) {
  const { id: ws } = useWorkspace();
  const q = useQuery({
    queryKey: ["ws", ws, "queries", "compare", leftId, rightId],
    queryFn: () => queries.compare(ws, { left_run_id: leftId, right_run_id: rightId }),
    enabled: open,
  });
  const grid = q.data ? compareToGrid(q.data) : null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="xl">
        <DialogHeader>
          <DialogTitle>Compare query runs</DialogTitle>
          <DialogDescription>
            Rows are matched on shared non-numeric columns; numeric columns show left, right, delta and percent change.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex min-h-[320px] flex-col gap-2">
          {q.isLoading ? (
            <LoadingState />
          ) : q.isError ? (
            <ErrorState error={q.error} onRetry={() => void q.refetch()} compact />
          ) : q.data && grid ? (
            <>
              <div className="flex flex-wrap items-center gap-2 text-xs text-fg-subtle">
                {q.data.identical ? <Badge tone="positive">Identical results</Badge> : <Badge tone="warning">{formatInt(q.data.changed_rows)} changed rows</Badge>}
                <span>Left: {formatInt(q.data.left_rows)} rows</span>
                <span>Right: {formatInt(q.data.right_rows)} rows</span>
                <span>Keys: {q.data.keys.join(", ") || "none"}</span>
                <span>Measures: {q.data.measures.join(", ") || "none"}</span>
              </div>
              <DataGrid columns={grid.columns} rows={grid.rows} height={380} className="rounded border border-border" />
            </>
          ) : null}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
