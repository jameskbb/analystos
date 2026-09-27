"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { datasets, relationships, type Cardinality } from "@/lib/api/resources/data";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { InlineError } from "@/components/states/states";
import { CARDINALITY_LABEL } from "./relationships";

/** Declares a relationship manually. Approved relationships are the only ones the semantic compiler joins on. */
export function AddRelationshipDialog({
  open,
  onOpenChange,
  defaultFromTable,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  defaultFromTable?: string;
}) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: qk.datasets(ws), queryFn: () => datasets.list(ws), enabled: open });
  const [fromTable, setFromTable] = React.useState(defaultFromTable ?? "");
  const [fromCol, setFromCol] = React.useState("");
  const [toTable, setToTable] = React.useState("");
  const [toCol, setToCol] = React.useState("");
  const [card, setCard] = React.useState<Cardinality>("many_to_one");
  const [approve, setApprove] = React.useState(true);
  const cols = (t: string) => list.data?.find((d) => d.table_name === t)?.columns ?? [];
  const create = useMutation({
    mutationFn: () =>
      relationships.create(ws, { from_table: fromTable, from_col: fromCol, to_table: toTable, to_col: toCol, cardinality: card, approve }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["ws", ws, "relationships"] });
      toast.success("Relationship added");
      onOpenChange(false);
      setFromCol("");
      setToCol("");
    },
  });
  const valid = fromTable && fromCol && toTable && toCol && !(fromTable === toTable && fromCol === toCol);
  const tables = list.data?.map((d) => d.table_name) ?? [];
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (valid) create.mutate();
          }}
        >
          <DialogHeader>
            <DialogTitle>Add relationship</DialogTitle>
            <DialogDescription>Declare how two tables join. AnalystOS analyzes the join for fan-out and orphans.</DialogDescription>
          </DialogHeader>
          <DialogBody className="grid gap-3 sm:grid-cols-2">
            <Field label="From table (many side)" htmlFor="rf-t">
              <NativeSelect id="rf-t" value={fromTable} onChange={(e) => { setFromTable(e.target.value); setFromCol(""); }}>
                <option value="">Select…</option>
                {tables.map((t) => <option key={t} value={t}>{t}</option>)}
              </NativeSelect>
            </Field>
            <Field label="From column" htmlFor="rf-c">
              <NativeSelect id="rf-c" value={fromCol} onChange={(e) => setFromCol(e.target.value)} disabled={!fromTable}>
                <option value="">Select…</option>
                {cols(fromTable).map((c) => <option key={c.name} value={c.name}>{c.name} ({c.type})</option>)}
              </NativeSelect>
            </Field>
            <Field label="To table (referenced)" htmlFor="rt-t">
              <NativeSelect id="rt-t" value={toTable} onChange={(e) => { setToTable(e.target.value); setToCol(""); }}>
                <option value="">Select…</option>
                {tables.map((t) => <option key={t} value={t}>{t}</option>)}
              </NativeSelect>
            </Field>
            <Field label="To column" htmlFor="rt-c">
              <NativeSelect id="rt-c" value={toCol} onChange={(e) => setToCol(e.target.value)} disabled={!toTable}>
                <option value="">Select…</option>
                {cols(toTable).map((c) => <option key={c.name} value={c.name}>{c.name} ({c.type})</option>)}
              </NativeSelect>
            </Field>
            <Field label="Cardinality" htmlFor="r-card">
              <NativeSelect id="r-card" value={card} onChange={(e) => setCard(e.target.value as Cardinality)}>
                {Object.entries(CARDINALITY_LABEL).map(([v, l]) => <option key={v} value={v}>{l} ({v.replace(/_/g, " ")})</option>)}
              </NativeSelect>
            </Field>
            <label className="flex items-center gap-2 self-end pb-1 text-sm">
              <Checkbox checked={approve} onCheckedChange={(v) => setApprove(v === true)} /> Approve now
            </label>
            <InlineError error={create.error} className="sm:col-span-2" />
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button variant="primary" type="submit" disabled={!valid || create.isPending}>Add relationship</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
