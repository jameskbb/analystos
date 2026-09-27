"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CheckCircle2, AlertTriangle, ShieldCheck } from "lucide-react";
import type { Metric } from "@/lib/api/types";
import { dimensions as dimensionsApi, metrics as metricsApi, semantic, type ModelIssue } from "@/lib/api/resources/semantic";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Segmented } from "@/components/ui/tabs";
import { InlineError } from "@/components/states/states";
import { Spinner } from "@/components/ui/spinner";
import {
  AGGS,
  TIME_AGGREGATIONS,
  FORMATS,
  emptyMetricForm,
  formFromMetric,
  formulaRefs,
  formulaSummary,
  metricPayloadFromForm,
  slugifyMetricId,
  validateMetricForm,
  type MetricForm,
  type MetricKind,
} from "./metric-utils";
import { cn } from "@/lib/utils";

export function IssueList({ issues, className }: { issues: ModelIssue[]; className?: string }) {
  if (!issues.length) return null;
  return (
    <ul className={cn("flex flex-col gap-1", className)} aria-label="Validation issues">
      {issues.map((i, idx) => (
        <li
          key={idx}
          className={cn(
            "flex items-start gap-1.5 rounded border px-2 py-1 text-xs",
            i.severity === "error" ? "border-negative/30 bg-negative-soft text-negative" : "border-warning/30 bg-warning-soft text-warning",
          )}
        >
          <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden />
          <span>
            <span className="font-mono">{i.object}</span>: {i.message}
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Create or edit a metric. Edits never overwrite: the API stores a new immutable version with the change note,
 * so historical analyses keep pointing at the version they used (spec §59).
 */
export function MetricFormDialog({
  open,
  onOpenChange,
  metric,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  metric?: Metric | null;
  onSaved?: (m: Metric) => void;
}) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  const editing = !!metric;
  const [form, setForm] = React.useState<MetricForm>(() => (metric ? formFromMetric(metric) : emptyMetricForm()));
  const [idTouched, setIdTouched] = React.useState(editing);
  const [showErrors, setShowErrors] = React.useState(false);
  const [issues, setIssues] = React.useState<ModelIssue[] | null>(null);

  React.useEffect(() => {
    if (open) {
      setForm(metric ? formFromMetric(metric) : emptyMetricForm());
      setIdTouched(!!metric);
      setShowErrors(false);
      setIssues(null);
    }
  }, [open, metric]);

  const entities = useQuery({ queryKey: qk.semantic(ws, "entities"), queryFn: () => semantic.entities(ws), enabled: open });
  const dims = useQuery({ queryKey: qk.dimensions(ws), queryFn: () => dimensionsApi.list(ws), enabled: open });
  const allMetrics = useQuery({ queryKey: qk.metrics(ws), queryFn: () => metricsApi.list(ws), enabled: open });
  const otherMetrics = (allMetrics.data ?? []).filter((m) => m.id !== form.id);
  const timeDims = (dims.data ?? []).filter((d) => d.type === "time" && (!form.entity || d.entity === form.entity || form.kind !== "simple"));

  const set = <K extends keyof MetricForm>(k: K, v: MetricForm[K]) => {
    setIssues(null);
    setForm((f) => {
      const next = { ...f, [k]: v };
      if (k === "label" && !idTouched && !editing) next.id = slugifyMetricId(String(v));
      return next;
    });
  };
  const errors = validateMetricForm(form);
  const err = (k: keyof MetricForm) => (showErrors ? errors[k] : undefined);

  const validate = useMutation({
    mutationFn: () => metricsApi.validate(ws, metricPayloadFromForm(form)),
    onSuccess: (res) => setIssues(res),
  });

  const save = useMutation({
    mutationFn: async () => {
      const payload = metricPayloadFromForm(form);
      if (editing && metric) {
        const { id: _id, ...rest } = payload;
        return metricsApi.update(ws, metric.id, { ...rest, change_note: form.change_note.trim() || "Edited definition" });
      }
      return metricsApi.create(ws, { ...payload, change_note: form.change_note.trim() || "Created" });
    },
    onSuccess: (m) => {
      void qc.invalidateQueries({ queryKey: qk.metrics(ws) });
      void qc.invalidateQueries({ queryKey: qk.semantic(ws, "model") });
      toast.success(editing ? `Saved ${m.label ?? m.id} as version ${m.version_no ?? m.version}` : `Created ${m.label ?? m.id}`);
      onOpenChange(false);
      onSaved?.(m);
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setShowErrors(true);
    if (Object.keys(errors).length) return;
    save.mutate();
  };

  const refs = form.kind === "derived" ? formulaRefs(form.formula, (allMetrics.data ?? []).map((m) => m.id)) : [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col" noValidate>
          <DialogHeader>
            <DialogTitle>{editing ? `Edit ${metric?.label ?? metric?.id}` : "Define a metric"}</DialogTitle>
            <DialogDescription>
              {editing
                ? `Saving creates version ${(metric?.version_no ?? metric?.version ?? 0) + 1}. Earlier versions stay available for reproducibility.`
                : "Metrics are the single source of truth for calculations across investigations, dashboards and reports."}
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="grid gap-3 sm:grid-cols-2">
            <Field label="Label" htmlFor="m-label" error={err("label")}>
              <Input id="m-label" value={form.label} onChange={(e) => set("label", e.target.value)} placeholder="Gross Margin %" autoFocus />
            </Field>
            <Field label="Metric id" htmlFor="m-id" error={err("id")} hint={editing ? "The id is fixed once created." : "Used in formulas, trees and the API."}>
              <Input
                id="m-id"
                className="font-mono"
                value={form.id}
                disabled={editing}
                onChange={(e) => {
                  setIdTouched(true);
                  set("id", e.target.value);
                }}
                placeholder="gross_margin_pct"
              />
            </Field>
            <Field label="Description" htmlFor="m-desc" className="sm:col-span-2">
              <Textarea id="m-desc" rows={2} value={form.description} onChange={(e) => set("description", e.target.value)} />
            </Field>

            <div className="flex flex-col gap-1 sm:col-span-2">
              <span className="text-xs font-medium text-fg-muted">Kind</span>
              <Segmented<MetricKind>
                aria-label="Metric kind"
                value={form.kind}
                onChange={(v) => set("kind", v)}
                options={[
                  { value: "simple", label: "Simple aggregation", title: "SUM/COUNT/AVG… of an expression at an entity's grain" },
                  { value: "ratio", label: "Ratio", title: "Ratio of two metrics, computed as a ratio of sums" },
                  { value: "derived", label: "Derived formula", title: "Arithmetic over other metrics" },
                ]}
              />
            </div>

            {form.kind === "simple" ? (
              <>
                <Field label="Entity (grain)" htmlFor="m-entity" error={err("entity")} hint="The table grain the aggregation runs at.">
                  <NativeSelect id="m-entity" value={form.entity} onChange={(e) => set("entity", e.target.value)}>
                    <option value="">Choose an entity…</option>
                    {(entities.data ?? []).map((en) => (
                      <option key={en.name} value={en.name}>
                        {en.label || en.name} ({en.table_name})
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
                <Field label="Aggregation" htmlFor="m-agg">
                  <NativeSelect id="m-agg" value={form.agg} onChange={(e) => set("agg", e.target.value as MetricForm["agg"])}>
                    {AGGS.map((a) => (
                      <option key={a} value={a}>
                        {a.replace("_", " ")}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
                <Field
                  label="Over time"
                  htmlFor="m-timeagg"
                  error={err("time_aggregation")}
                  hint={`How to combine values over time. ${TIME_AGGREGATIONS.find((t) => t.value === form.time_aggregation)?.help ?? ""}`}
                >
                  <NativeSelect
                    id="m-timeagg"
                    value={form.time_aggregation}
                    onChange={(e) => set("time_aggregation", e.target.value as MetricForm["time_aggregation"])}
                  >
                    {TIME_AGGREGATIONS.map((t) => (
                      <option key={t.value} value={t.value}>
                        {t.label}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
                <Field
                  label="Expression"
                  htmlFor="m-expr"
                  className="sm:col-span-2"
                  error={err("expr")}
                  hint="A SQL expression over the entity's columns, e.g. net_amount or qty * list_price."
                >
                  <Input id="m-expr" className="font-mono" value={form.expr} onChange={(e) => set("expr", e.target.value)} placeholder="net_amount" />
                </Field>
              </>
            ) : null}

            {form.kind === "ratio" ? (
              <>
                <Field label="Numerator metric" htmlFor="m-num" error={err("numerator")}>
                  <NativeSelect id="m-num" value={form.numerator} onChange={(e) => set("numerator", e.target.value)}>
                    <option value="">Choose…</option>
                    {otherMetrics.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.label ?? m.id}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
                <Field label="Denominator metric" htmlFor="m-den" error={err("denominator")}>
                  <NativeSelect id="m-den" value={form.denominator} onChange={(e) => set("denominator", e.target.value)}>
                    <option value="">Choose…</option>
                    {otherMetrics.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.label ?? m.id}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
                <p className="text-xs text-fg-subtle sm:col-span-2">
                  Ratios are computed as a ratio of sums at the query grain, never as an average of row-level ratios.
                </p>
              </>
            ) : null}

            {form.kind === "derived" ? (
              <Field
                label="Formula"
                htmlFor="m-formula"
                className="sm:col-span-2"
                error={err("formula")}
                hint={refs.length ? `References: ${refs.join(", ")}` : "Arithmetic over metric ids, e.g. revenue - cogs."}
              >
                <Input id="m-formula" className="font-mono" value={form.formula} onChange={(e) => set("formula", e.target.value)} placeholder="revenue - cogs" />
              </Field>
            ) : null}

            <Field label="Format" htmlFor="m-format">
              <NativeSelect id="m-format" value={form.format} onChange={(e) => set("format", e.target.value as MetricForm["format"])}>
                {FORMATS.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Default time dimension" htmlFor="m-time" hint="Used for trends and period comparisons.">
              <NativeSelect id="m-time" value={form.default_time_dimension} onChange={(e) => set("default_time_dimension", e.target.value)}>
                <option value="">Entity default</option>
                {timeDims.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.label || d.name}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Owner" htmlFor="m-owner">
              <Input id="m-owner" value={form.owner} onChange={(e) => set("owner", e.target.value)} placeholder="FP&A" />
            </Field>
            <Field label="Tags" htmlFor="m-tags" hint="Comma-separated.">
              <Input id="m-tags" value={form.tags} onChange={(e) => set("tags", e.target.value)} placeholder="revenue, finance" />
            </Field>
            <Field label="Synonyms" htmlFor="m-syn" className="sm:col-span-2" hint="Words users may type in questions. Shared synonyms are flagged as ambiguous.">
              <Input id="m-syn" value={form.synonyms} onChange={(e) => set("synonyms", e.target.value)} placeholder="sales, net sales" />
            </Field>
            <div className="flex flex-wrap gap-4 sm:col-span-2">
              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={form.canonical} onCheckedChange={(v) => set("canonical", v === true)} aria-label="Canonical definition" />
                Canonical definition
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={form.higher_is_better} onCheckedChange={(v) => set("higher_is_better", v === true)} aria-label="Higher is better" />
                Higher is better
              </label>
            </div>
            {editing ? (
              <Field label="Change note" htmlFor="m-note" className="sm:col-span-2" hint="Recorded on the new version.">
                <Input id="m-note" value={form.change_note} onChange={(e) => set("change_note", e.target.value)} placeholder="Exclude cancelled orders" />
              </Field>
            ) : null}

            <div className="rounded border border-border bg-bg-subtle px-2.5 py-1.5 sm:col-span-2">
              <div className="text-2xs font-semibold tracking-wider text-fg-subtle uppercase">Definition</div>
              <code className="font-mono text-xs">{formulaSummary(metricPayloadFromForm(form))}</code>
            </div>
            {issues ? (
              issues.length ? (
                <IssueList issues={issues} className="sm:col-span-2" />
              ) : (
                <p className="flex items-center gap-1.5 text-xs text-positive sm:col-span-2">
                  <CheckCircle2 className="size-3.5" /> The definition is valid against the current semantic model.
                </p>
              )
            ) : null}
            <InlineError error={validate.error ?? save.error} className="sm:col-span-2" />
          </DialogBody>
          <DialogFooter>
            <Button
              variant="ghost"
              className="mr-auto"
              onClick={() => {
                setShowErrors(true);
                if (!Object.keys(errors).length) validate.mutate();
              }}
              disabled={validate.isPending}
            >
              {validate.isPending ? <Spinner /> : <ShieldCheck />} Validate
            </Button>
            <Button variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" disabled={save.isPending}>
              {save.isPending ? "Saving…" : editing ? "Save new version" : "Create metric"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
