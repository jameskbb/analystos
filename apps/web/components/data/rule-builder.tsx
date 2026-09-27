"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { datasets, quality } from "@/lib/api/resources/data";
import { qk } from "@/lib/api/keys";
import type { QualityRuleKind } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { InlineError } from "@/components/states/states";
import {
  RULE_KINDS,
  buildRuleParams,
  defaultRuleName,
  emptyDraft,
  ruleKindMeta,
  validateRule,
  type RuleDraft,
  type RuleErrors,
} from "./rule-params";

/** The per-kind parameter inputs. Exported for tests. */
export function RuleParamFields({
  draft,
  set,
  errors,
  tables,
  columnsOf,
}: {
  draft: RuleDraft;
  set: (patch: Partial<RuleDraft>) => void;
  errors: RuleErrors;
  tables: string[];
  columnsOf: (table: string) => { name: string; type: string }[];
}) {
  switch (draft.kind) {
    case "range":
    case "between":
      return (
        <>
          <Field label={draft.kind === "range" ? "Minimum (≥, optional)" : "Minimum"} htmlFor="rp-min" error={errors.min}>
            <Input id="rp-min" inputMode="decimal" value={draft.min} onChange={(e) => set({ min: e.target.value })} placeholder="0" />
          </Field>
          <Field label={draft.kind === "range" ? "Maximum (≤, optional)" : "Maximum"} htmlFor="rp-max" error={errors.max}>
            <Input id="rp-max" inputMode="decimal" value={draft.max} onChange={(e) => set({ max: e.target.value })} placeholder={draft.kind === "between" ? "1" : ""} />
          </Field>
        </>
      );
    case "fk_exists":
      return (
        <>
          <Field label="Referenced table" htmlFor="rp-rt" error={errors.ref_table}>
            <NativeSelect id="rp-rt" value={draft.ref_table} onChange={(e) => set({ ref_table: e.target.value, ref_column: "" })}>
              <option value="">Select…</option>
              {tables.map((t) => <option key={t} value={t}>{t}</option>)}
            </NativeSelect>
          </Field>
          <Field label="Referenced column" htmlFor="rp-rc" error={errors.ref_column}>
            <NativeSelect id="rp-rc" value={draft.ref_column} onChange={(e) => set({ ref_column: e.target.value })} disabled={!draft.ref_table}>
              <option value="">Select…</option>
              {columnsOf(draft.ref_table).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
            </NativeSelect>
          </Field>
        </>
      );
    case "allowed_values":
      return (
        <Field label="Allowed values (one per line or comma-separated)" htmlFor="rp-vals" error={errors.values} className="sm:col-span-2">
          <Textarea id="rp-vals" rows={4} value={draft.values} onChange={(e) => set({ values: e.target.value })} placeholder={"Enterprise\nContractor\nRetail"} />
        </Field>
      );
    case "regex":
      return (
        <Field label="Pattern" htmlFor="rp-re" error={errors.pattern} hint="Values must fully match this regular expression." className="sm:col-span-2">
          <Input id="rp-re" className="font-mono" value={draft.pattern} onChange={(e) => set({ pattern: e.target.value })} placeholder="^[A-Z]{3}-\d{4}$" />
        </Field>
      );
    case "custom_sql":
      return (
        <Field
          label="SQL returning failing rows"
          htmlFor="rp-sql"
          error={errors.sql}
          hint="Read-only SELECT. Every row it returns counts as a failure."
          className="sm:col-span-2"
        >
          <Textarea
            id="rp-sql"
            rows={6}
            className="font-mono text-xs"
            spellCheck={false}
            value={draft.sql}
            onChange={(e) => set({ sql: e.target.value })}
            placeholder={`SELECT * FROM ${draft.table_name || "orders"} WHERE ship_date < order_date`}
          />
        </Field>
      );
    default:
      return null;
  }
}

/** DQ rule builder (spec §11): not null, unique, range, between, FK exists, not future, allowed set, regex, custom SQL. */
export function RuleBuilderDialog({
  open,
  onOpenChange,
  defaultTable,
  defaultKind,
  defaultColumn,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  defaultTable?: string;
  defaultKind?: QualityRuleKind;
  defaultColumn?: string;
}) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: qk.datasets(ws), queryFn: () => datasets.list(ws), enabled: open });
  const [draft, setDraft] = React.useState<RuleDraft>(() => ({ ...emptyDraft(defaultTable, defaultKind), column: defaultColumn ?? "" }));
  const [touched, setTouched] = React.useState(false);
  const [nameEdited, setNameEdited] = React.useState(false);

  React.useEffect(() => {
    if (open) {
      setDraft({ ...emptyDraft(defaultTable, defaultKind), column: defaultColumn ?? "" });
      setTouched(false);
      setNameEdited(false);
    }
  }, [open, defaultTable, defaultKind, defaultColumn]);

  const set = (patch: Partial<RuleDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const errors = validateRule(draft);
  const valid = Object.keys(errors).length === 0;
  const tables = list.data?.map((d) => d.table_name) ?? [];
  const columnsOf = (t: string) => list.data?.find((d) => d.table_name === t)?.columns ?? [];
  const meta = ruleKindMeta(draft.kind);
  const name = nameEdited ? draft.name : valid ? defaultRuleName(draft) : draft.name;

  const create = useMutation({
    mutationFn: () =>
      quality.createRule(ws, {
        table_name: draft.table_name,
        dataset_id: list.data?.find((d) => d.table_name === draft.table_name)?.id ?? null,
        name: name.trim() || defaultRuleName(draft),
        kind: draft.kind,
        column: meta?.needsColumn ? draft.column : null,
        params: buildRuleParams(draft),
        severity: draft.severity,
        description: draft.description,
      }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["ws", ws, "quality"] });
      toast.success(`Rule created: ${r.name}`);
      onOpenChange(false);
    },
  });

  const shownErrors = touched ? errors : {};
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            setTouched(true);
            if (valid) create.mutate();
          }}
        >
          <DialogHeader>
            <DialogTitle>New quality rule</DialogTitle>
            <DialogDescription>Rules only detect problems. AnalystOS never modifies your data.</DialogDescription>
          </DialogHeader>
          <DialogBody className="grid gap-3 sm:grid-cols-2">
            <Field label="Table" htmlFor="r-table" error={shownErrors.table_name}>
              <NativeSelect id="r-table" value={draft.table_name} onChange={(e) => set({ table_name: e.target.value, column: "" })}>
                <option value="">Select…</option>
                {tables.map((t) => <option key={t} value={t}>{t}</option>)}
              </NativeSelect>
            </Field>
            <Field label="Check" htmlFor="r-kind" hint={meta?.description}>
              <NativeSelect id="r-kind" value={draft.kind} onChange={(e) => set({ kind: e.target.value as QualityRuleKind })}>
                {RULE_KINDS.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
              </NativeSelect>
            </Field>
            {meta?.needsColumn ? (
              <Field label="Column" htmlFor="r-col" error={shownErrors.column}>
                <NativeSelect id="r-col" value={draft.column} onChange={(e) => set({ column: e.target.value })} disabled={!draft.table_name}>
                  <option value="">Select…</option>
                  {columnsOf(draft.table_name).map((c) => <option key={c.name} value={c.name}>{c.name} ({c.type})</option>)}
                </NativeSelect>
              </Field>
            ) : null}
            <Field label="Severity" htmlFor="r-sev">
              <NativeSelect id="r-sev" value={draft.severity} onChange={(e) => set({ severity: e.target.value as RuleDraft["severity"] })}>
                <option value="info">Info</option>
                <option value="warning">Warning</option>
                <option value="error">Error</option>
              </NativeSelect>
            </Field>
            <RuleParamFields draft={draft} set={set} errors={shownErrors} tables={tables} columnsOf={columnsOf} />
            <Field label="Rule name" htmlFor="r-name" className="sm:col-span-2">
              <Input
                id="r-name"
                value={name}
                onChange={(e) => {
                  setNameEdited(true);
                  set({ name: e.target.value });
                }}
                placeholder="Generated from the check"
              />
            </Field>
            <Field label="Description (optional)" htmlFor="r-desc" className="sm:col-span-2">
              <Input id="r-desc" value={draft.description} onChange={(e) => set({ description: e.target.value })} />
            </Field>
            <InlineError error={create.error} className="sm:col-span-2" />
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button variant="primary" type="submit" disabled={create.isPending || (touched && !valid)}>
              Create rule
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
