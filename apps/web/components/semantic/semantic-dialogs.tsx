"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { Dimension, Entity, GlossaryTerm } from "@/lib/api/types";
import { dimensions as dimensionsApi, glossary as glossaryApi, metrics as metricsApi, semantic } from "@/lib/api/resources/semantic";
import { datasets as datasetsApi } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { InlineError } from "@/components/states/states";
import { splitList } from "./metric-utils";

const TIME_GRAINS = ["day", "week", "month", "quarter", "year"];

function DialogShell({
  open,
  onOpenChange,
  title,
  description,
  onSubmit,
  pending,
  error,
  submitLabel,
  children,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description?: string;
  onSubmit: () => void;
  pending: boolean;
  error: unknown;
  submitLabel: string;
  children: React.ReactNode;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit();
          }}
        >
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            {description ? <DialogDescription>{description}</DialogDescription> : null}
          </DialogHeader>
          <DialogBody className="grid gap-3 sm:grid-cols-2">
            {children}
            <InlineError error={error} className="sm:col-span-2" />
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" disabled={pending}>
              {pending ? "Saving…" : submitLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function DimensionDialog({
  open,
  onOpenChange,
  dimension,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  dimension?: Dimension | null;
}) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  const editing = !!dimension;
  const blank = { name: "", label: "", entity: "", expr: "", type: "categorical", time_grains: [] as string[], description: "", synonyms: "" };
  const [f, setF] = React.useState(blank);
  React.useEffect(() => {
    if (!open) return;
    setF(
      dimension
        ? {
            name: dimension.name,
            label: dimension.label ?? "",
            entity: dimension.entity,
            expr: dimension.expr,
            type: dimension.type,
            time_grains: dimension.time_grains ?? [],
            description: dimension.description ?? "",
            synonyms: (dimension.synonyms ?? []).join(", "),
          }
        : blank,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, dimension]);
  const entities = useQuery({ queryKey: qk.semantic(ws, "entities"), queryFn: () => semantic.entities(ws), enabled: open });
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: f.name.trim(),
        label: f.label.trim() || undefined,
        entity: f.entity,
        expr: f.expr.trim(),
        type: f.type,
        time_grains: f.type === "time" ? f.time_grains : [],
        description: f.description.trim() || undefined,
        synonyms: splitList(f.synonyms),
      };
      return editing ? dimensionsApi.update(ws, dimension!.name, body) : dimensionsApi.create(ws, body);
    },
    onSuccess: (d) => {
      void qc.invalidateQueries({ queryKey: qk.dimensions(ws) });
      void qc.invalidateQueries({ queryKey: qk.semantic(ws, "model") });
      toast.success(`Saved dimension ${d.name}`);
      onOpenChange(false);
    },
  });
  return (
    <DialogShell
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? `Edit dimension ${dimension?.name}` : "New dimension"}
      description="Dimensions are the ways metrics can be sliced. They belong to an entity; joins follow approved relationships only."
      onSubmit={() => save.mutate()}
      pending={save.isPending}
      error={save.error}
      submitLabel={editing ? "Save" : "Create dimension"}
    >
      <Field label="Name" htmlFor="d-name">
        <Input id="d-name" className="font-mono" disabled={editing} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required />
      </Field>
      <Field label="Label" htmlFor="d-label">
        <Input id="d-label" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} />
      </Field>
      <Field label="Entity" htmlFor="d-entity">
        <NativeSelect id="d-entity" value={f.entity} onChange={(e) => setF({ ...f, entity: e.target.value })} required>
          <option value="">Choose…</option>
          {(entities.data ?? []).map((en) => (
            <option key={en.name} value={en.name}>
              {en.label || en.name}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field label="Type" htmlFor="d-type">
        <NativeSelect id="d-type" value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>
          <option value="categorical">Categorical</option>
          <option value="time">Time</option>
          <option value="numeric">Numeric</option>
        </NativeSelect>
      </Field>
      <Field label="Expression" htmlFor="d-expr" className="sm:col-span-2" hint="Column or SQL expression on the entity's table.">
        <Input id="d-expr" className="font-mono" value={f.expr} onChange={(e) => setF({ ...f, expr: e.target.value })} required />
      </Field>
      {f.type === "time" ? (
        <fieldset className="flex flex-wrap gap-3 sm:col-span-2">
          <legend className="mb-1 text-xs font-medium text-fg-muted">Time grains</legend>
          {TIME_GRAINS.map((g) => (
            <label key={g} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={f.time_grains.includes(g)}
                onChange={(e) =>
                  setF({ ...f, time_grains: e.target.checked ? [...f.time_grains, g] : f.time_grains.filter((x) => x !== g) })
                }
              />
              {g}
            </label>
          ))}
        </fieldset>
      ) : null}
      <Field label="Synonyms" htmlFor="d-syn" className="sm:col-span-2" hint="Comma-separated.">
        <Input id="d-syn" value={f.synonyms} onChange={(e) => setF({ ...f, synonyms: e.target.value })} />
      </Field>
      <Field label="Description" htmlFor="d-desc" className="sm:col-span-2">
        <Textarea id="d-desc" rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      </Field>
    </DialogShell>
  );
}

export function EntityDialog({ open, onOpenChange, entity }: { open: boolean; onOpenChange: (o: boolean) => void; entity?: Entity | null }) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  const editing = !!entity;
  const blank = { name: "", label: "", table: "", primary_key: "", grain_description: "", description: "" };
  const [f, setF] = React.useState(blank);
  React.useEffect(() => {
    if (!open) return;
    setF(
      entity
        ? {
            name: entity.name,
            label: entity.label ?? "",
            table: entity.table_name,
            primary_key: Array.isArray(entity.primary_key) ? entity.primary_key.join(", ") : entity.primary_key,
            grain_description: entity.grain_description ?? "",
            description: entity.description ?? "",
          }
        : blank,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, entity]);
  const datasets = useQuery({ queryKey: qk.datasets(ws), queryFn: () => datasetsApi.list(ws), enabled: open });
  const table = (datasets.data ?? []).find((d) => d.table_name === f.table);
  const save = useMutation({
    mutationFn: () => {
      const keys = splitList(f.primary_key);
      return semantic.upsertEntity(ws, {
        name: f.name.trim(),
        label: f.label.trim() || null,
        table: f.table,
        primary_key: keys.length === 1 ? keys[0] : keys,
        grain_description: f.grain_description.trim(),
        description: f.description.trim() || null,
      });
    },
    onSuccess: (en) => {
      void qc.invalidateQueries({ queryKey: qk.semantic(ws, "entities") });
      void qc.invalidateQueries({ queryKey: qk.semantic(ws, "model") });
      toast.success(`Saved entity ${en.name}`);
      onOpenChange(false);
    },
  });
  return (
    <DialogShell
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? `Edit entity ${entity?.name}` : "New entity"}
      description="An entity binds a table to its grain: one row per primary key. The compiler uses the grain to prevent fan-out double counting."
      onSubmit={() => save.mutate()}
      pending={save.isPending}
      error={save.error}
      submitLabel={editing ? "Save" : "Create entity"}
    >
      <Field label="Name" htmlFor="e-name">
        <Input id="e-name" className="font-mono" disabled={editing} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required />
      </Field>
      <Field label="Label" htmlFor="e-label">
        <Input id="e-label" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} />
      </Field>
      <Field label="Table" htmlFor="e-table">
        <NativeSelect id="e-table" value={f.table} onChange={(e) => setF({ ...f, table: e.target.value })} required>
          <option value="">Choose a dataset table…</option>
          {(datasets.data ?? []).map((d) => (
            <option key={d.id} value={d.table_name}>
              {d.table_name}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field label="Primary key" htmlFor="e-pk" hint="Comma-separate composite keys.">
        <Input id="e-pk" className="font-mono" list="e-pk-cols" value={f.primary_key} onChange={(e) => setF({ ...f, primary_key: e.target.value })} required />
        <datalist id="e-pk-cols">
          {(table?.columns ?? []).map((c) => (
            <option key={c.name} value={c.name} />
          ))}
        </datalist>
      </Field>
      <Field label="Grain" htmlFor="e-grain" className="sm:col-span-2" hint='Plain-language grain, e.g. "one row per order line".'>
        <Input id="e-grain" value={f.grain_description} onChange={(e) => setF({ ...f, grain_description: e.target.value })} />
      </Field>
      <Field label="Description" htmlFor="e-desc" className="sm:col-span-2">
        <Textarea id="e-desc" rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      </Field>
    </DialogShell>
  );
}

export function GlossaryDialog({ open, onOpenChange, term }: { open: boolean; onOpenChange: (o: boolean) => void; term?: GlossaryTerm | null }) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  const editing = !!term;
  const blank = { term: "", definition: "", formula: "", metric_id: "", related: "", synonyms: "", candidates: [] as string[] };
  const [f, setF] = React.useState(blank);
  React.useEffect(() => {
    if (!open) return;
    setF(
      term
        ? {
            term: term.term,
            definition: term.definition,
            formula: term.formula ?? "",
            metric_id: term.metric_id ?? "",
            related: (term.related ?? []).join(", "),
            synonyms: (term.synonyms ?? []).join(", "),
            candidates: term.candidate_metric_ids ?? [],
          }
        : blank,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, term]);
  const metrics = useQuery({ queryKey: qk.metrics(ws), queryFn: () => metricsApi.list(ws), enabled: open });
  const save = useMutation({
    mutationFn: () => {
      const body = {
        term: f.term.trim(),
        definition: f.definition.trim(),
        formula: f.formula.trim() || null,
        metric_id: f.metric_id || null,
        related: splitList(f.related),
        synonyms: splitList(f.synonyms),
        candidate_metric_ids: f.metric_id ? [] : f.candidates,
      };
      return editing ? glossaryApi.update(ws, term!.id, body) : glossaryApi.create(ws, body);
    },
    onSuccess: (t) => {
      void qc.invalidateQueries({ queryKey: qk.glossary(ws) });
      toast.success(`Saved “${t.term}”`);
      onOpenChange(false);
    },
  });
  return (
    <DialogShell
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? `Edit “${term?.term}”` : "New glossary term"}
      description="Question interpretation uses the glossary. Link a term to one metric, or to several candidates to make it deliberately ambiguous so AnalystOS asks instead of guessing."
      onSubmit={() => save.mutate()}
      pending={save.isPending}
      error={save.error}
      submitLabel={editing ? "Save" : "Add term"}
    >
      <Field label="Term" htmlFor="g-term">
        <Input id="g-term" value={f.term} onChange={(e) => setF({ ...f, term: e.target.value })} required />
      </Field>
      <Field label="Linked metric" htmlFor="g-metric">
        <NativeSelect id="g-metric" value={f.metric_id} onChange={(e) => setF({ ...f, metric_id: e.target.value })}>
          <option value="">None / ambiguous</option>
          {(metrics.data ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.label ?? m.id}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field label="Definition" htmlFor="g-def" className="sm:col-span-2">
        <Textarea id="g-def" rows={3} value={f.definition} onChange={(e) => setF({ ...f, definition: e.target.value })} required />
      </Field>
      <Field label="Formula" htmlFor="g-formula" className="sm:col-span-2">
        <Input id="g-formula" className="font-mono" value={f.formula} onChange={(e) => setF({ ...f, formula: e.target.value })} />
      </Field>
      {!f.metric_id ? (
        <fieldset className="sm:col-span-2">
          <legend className="mb-1 text-xs font-medium text-fg-muted">Candidate metrics (ambiguous term)</legend>
          <div className="flex max-h-32 flex-wrap gap-x-4 gap-y-1 overflow-auto scrollbar-thin">
            {(metrics.data ?? []).map((m) => (
              <label key={m.id} className="flex items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  checked={f.candidates.includes(m.id)}
                  onChange={(e) =>
                    setF({ ...f, candidates: e.target.checked ? [...f.candidates, m.id] : f.candidates.filter((x) => x !== m.id) })
                  }
                />
                {m.label ?? m.id}
              </label>
            ))}
          </div>
        </fieldset>
      ) : null}
      <Field label="Synonyms" htmlFor="g-syn" hint="Comma-separated.">
        <Input id="g-syn" value={f.synonyms} onChange={(e) => setF({ ...f, synonyms: e.target.value })} />
      </Field>
      <Field label="Related terms" htmlFor="g-rel" hint="Comma-separated.">
        <Input id="g-rel" value={f.related} onChange={(e) => setF({ ...f, related: e.target.value })} />
      </Field>
    </DialogShell>
  );
}
