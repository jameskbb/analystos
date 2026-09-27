"use client";
import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, CheckCircle2, Database, KeyRound, PlugZap, ShieldCheck, XCircle } from "lucide-react";
import { dataSources, type ConnectionTest, type ConnectorKindSpec } from "@/lib/api/resources/data";
import { qk } from "@/lib/api/keys";
import type { DataSource } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import { DataGrid } from "@/components/data-grid/data-grid";
import { ErrorState, InlineError, LoadingState, EmptyState } from "@/components/states/states";
import { formatDuration, formatInt } from "@/lib/format";
import { cn } from "@/lib/utils";
import { connectorFields, initialValues, splitValues, type FormValues } from "./connector-form";
import { JobProgress, useJobRunner } from "./use-job";
import { TABLE_NAME_RE, suggestTableName } from "./naming";

const kindsKey = ["connector-kinds"] as const;

export function TestResult({ result }: { result: ConnectionTest | null | undefined }) {
  if (!result) return null;
  return (
    <div
      role="status"
      className={cn(
        "flex items-start gap-2 rounded border px-2.5 py-1.5 text-sm",
        result.ok ? "border-positive/30 bg-positive-soft text-positive" : "border-negative/30 bg-negative-soft text-negative",
      )}
    >
      {result.ok ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0" /> : <XCircle className="mt-0.5 size-3.5 shrink-0" />}
      <div className="min-w-0">
        <div className="break-words">{result.message}</div>
        <div className="text-xs opacity-80">
          {[
            result.latency_ms != null ? formatDuration(result.latency_ms) : null,
            result.server_version,
            result.read_only_enforced ? "read-only session enforced" : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </div>
      </div>
    </div>
  );
}

function ConnectorForm({
  spec,
  onBack,
  onSaved,
}: {
  spec: ConnectorKindSpec;
  onBack: () => void;
  onSaved: (s: DataSource) => void;
}) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  const fields = React.useMemo(() => connectorFields(spec), [spec]);
  const [name, setName] = React.useState(`${spec.label} connection`);
  const [values, setValues] = React.useState<FormValues>(() => initialValues(fields));
  const [missing, setMissing] = React.useState<string[]>([]);
  const body = () => {
    const { config, secrets, missing: m } = splitValues(fields, values);
    setMissing(m);
    if (m.length) throw new Error(`Required: ${m.join(", ")}`);
    return { name: name.trim() || spec.label, kind: spec.kind, config, secrets };
  };
  const test = useMutation({ mutationFn: () => dataSources.testSettings(ws, body()) });
  const save = useMutation({
    mutationFn: () => dataSources.create(ws, body()),
    onSuccess: (s) => {
      void qc.invalidateQueries({ queryKey: qk.dataSources(ws) });
      toast.success(`Saved ${s.name}`);
      onSaved(s);
    },
  });
  return (
    <form
      className="flex min-h-0 flex-1 flex-col"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <DialogBody className="flex flex-col gap-3">
        {spec.config_schema?.description ? <p className="text-xs text-fg-subtle">{spec.config_schema.description}</p> : null}
        <Field label="Connection name" htmlFor="src-name">
          <Input id="src-name" value={name} onChange={(e) => setName(e.target.value)} required />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          {fields.map((f) => {
            const id = `f-${f.name}`;
            const label = (
              <span className="inline-flex items-center gap-1">
                {f.label}
                {f.required ? <span className="text-negative">*</span> : null}
                {f.secret ? <KeyRound className="size-3 text-fg-subtle" aria-label="secret" /> : null}
              </span>
            );
            const invalid = missing.includes(f.label);
            if (f.input === "checkbox")
              return (
                <label key={f.name} className="flex items-center gap-2 text-sm sm:col-span-2">
                  <Checkbox checked={values[f.name] === true} onCheckedChange={(v) => setValues((p) => ({ ...p, [f.name]: v === true }))} />
                  {label}
                  {f.help ? <span className="text-xs text-fg-subtle">{f.help}</span> : null}
                </label>
              );
            return (
              <Field
                key={f.name}
                label={label}
                htmlFor={id}
                hint={f.secret ? "Encrypted at rest; never shown again or written to logs." : f.help}
                error={invalid ? "Required" : undefined}
                className={f.input === "textarea" ? "sm:col-span-2" : undefined}
              >
                {f.input === "select" ? (
                  <NativeSelect
                    id={id}
                    value={String(values[f.name] ?? "")}
                    onChange={(e) => setValues((p) => ({ ...p, [f.name]: e.target.value }))}
                  >
                    {!f.required ? <option value="">(default)</option> : null}
                    {f.options?.map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </NativeSelect>
                ) : f.input === "textarea" ? (
                  <Textarea
                    id={id}
                    className="font-mono text-xs"
                    rows={4}
                    autoComplete="off"
                    spellCheck={false}
                    value={String(values[f.name] ?? "")}
                    onChange={(e) => setValues((p) => ({ ...p, [f.name]: e.target.value }))}
                  />
                ) : (
                  <Input
                    id={id}
                    type={f.input === "password" ? "password" : f.input === "number" ? "number" : "text"}
                    autoComplete={f.secret ? "new-password" : "off"}
                    aria-invalid={invalid || undefined}
                    value={String(values[f.name] ?? "")}
                    onChange={(e) => setValues((p) => ({ ...p, [f.name]: e.target.value }))}
                  />
                )}
              </Field>
            );
          })}
        </div>
        <p className="flex items-center gap-1.5 text-xs text-fg-subtle">
          <ShieldCheck className="size-3.5" /> AnalystOS only runs read-only queries against connected databases.
        </p>
        <TestResult result={test.data} />
        <InlineError error={test.error ?? save.error} />
      </DialogBody>
      <DialogFooter>
        <Button variant="ghost" onClick={onBack} className="mr-auto">
          <ArrowLeft /> Connectors
        </Button>
        <Button variant="secondary" onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? <Spinner /> : <PlugZap />} Test connection
        </Button>
        <Button variant="primary" type="submit" disabled={save.isPending}>
          Save connection
        </Button>
      </DialogFooter>
    </form>
  );
}

/** Connect a database (spec §8): pick a connector, fill the schema-driven form, test, save, then browse tables. */
export function ConnectDatabaseDialog({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved?: (s: DataSource) => void;
}) {
  const kinds = useQuery({ queryKey: kindsKey, queryFn: dataSources.kinds, enabled: open, staleTime: 5 * 60_000 });
  const [kind, setKind] = React.useState<string | null>(null);
  const spec = kinds.data?.find((k) => k.kind === kind) ?? null;
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) setKind(null);
      }}
    >
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{spec ? `Connect ${spec.label}` : "Connect a database"}</DialogTitle>
          <DialogDescription>
            Credentials are encrypted with the server key and never returned by the API.
          </DialogDescription>
        </DialogHeader>
        {spec ? (
          <ConnectorForm
            key={spec.kind}
            spec={spec}
            onBack={() => setKind(null)}
            onSaved={(s) => {
              onOpenChange(false);
              setKind(null);
              onSaved?.(s);
            }}
          />
        ) : (
          <DialogBody>
            {kinds.isLoading ? (
              <LoadingState rows={3} />
            ) : kinds.error ? (
              <ErrorState error={kinds.error} onRetry={() => void kinds.refetch()} compact />
            ) : !kinds.data?.length ? (
              <EmptyState compact icon={Database} title="No connectors available" description="The server reports no database drivers." />
            ) : (
              <ul className="grid gap-2 sm:grid-cols-2">
                {kinds.data.map((k) => (
                  <li key={k.kind}>
                    <button
                      type="button"
                      onClick={() => setKind(k.kind)}
                      className="flex w-full items-center gap-2.5 rounded border border-border px-3 py-2.5 text-left hover:border-accent hover:bg-bg-subtle"
                    >
                      <Database className="size-4 text-fg-subtle" aria-hidden />
                      <span className="min-w-0">
                        <span className="block text-sm font-medium">{k.label}</span>
                        <span className="block truncate text-xs text-fg-subtle">
                          {Object.keys(k.config_schema?.properties ?? {}).length} settings
                          {k.secret_fields.length ? ` · ${k.secret_fields.length} secret` : ""}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </DialogBody>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Browse a saved connection: schemas → tables → preview → snapshot into the workspace store. */
export function SourceBrowserDialog({
  source,
  onOpenChange,
}: {
  source: DataSource | null;
  onOpenChange: (o: boolean) => void;
}) {
  const { id: ws, href } = useWorkspace();
  const qc = useQueryClient();
  const open = !!source;
  const sid = source?.id ?? "";
  const [schema, setSchema] = React.useState<string | null>(null);
  const [table, setTable] = React.useState<string | null>(null);
  const [targetName, setTargetName] = React.useState("");
  const [rowLimit, setRowLimit] = React.useState("");
  const [created, setCreated] = React.useState<string | null>(null);
  const { run, progress } = useJobRunner();

  React.useEffect(() => {
    setSchema(null);
    setTable(null);
    setCreated(null);
  }, [sid]);

  const test = useMutation({
    mutationFn: () => dataSources.test(ws, sid),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.dataSources(ws) }),
  });
  const schemas = useQuery({ queryKey: ["ws", ws, "data-sources", sid, "schemas"], queryFn: () => dataSources.schemas(ws, sid), enabled: open });
  const tables = useQuery({
    queryKey: ["ws", ws, "data-sources", sid, "tables", schema],
    queryFn: () => dataSources.tables(ws, sid, schema!),
    enabled: open && !!schema,
  });
  const preview = useQuery({
    queryKey: ["ws", ws, "data-sources", sid, "preview", schema, table],
    queryFn: () => dataSources.previewTable(ws, sid, schema!, table!, 100),
    enabled: open && !!schema && !!table,
  });
  const snapshot = useMutation({
    mutationFn: async () => {
      const limit = rowLimit.trim() ? Number(rowLimit) : null;
      const job = await run(() =>
        dataSources.importTable(ws, sid, {
          schema_name: schema!,
          table: table!,
          table_name: targetName,
          dataset_name: table,
          row_limit: limit,
        }),
      );
      return String(job.result?.dataset_id ?? job.resource_id ?? "");
    },
    onSuccess: (id) => {
      setCreated(id);
      void qc.invalidateQueries({ queryKey: qk.datasets(ws) });
      toast.success(`Snapshot of ${schema}.${table} ingested`);
    },
  });

  React.useEffect(() => {
    if (table) setTargetName(suggestTableName(table));
  }, [table]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="xl">
        <DialogHeader>
          <DialogTitle>{source?.name}</DialogTitle>
          <DialogDescription>
            {source?.kind} · status {source?.status}
            {source?.secret_fields?.length ? ` · secrets stored: ${source.secret_fields.join(", ")}` : ""}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <Button size="xs" onClick={() => test.mutate()} disabled={test.isPending}>
              {test.isPending ? <Spinner /> : <PlugZap />} Test connection
            </Button>
            <div className="min-w-0 flex-1">
              <TestResult result={test.data} />
              <InlineError error={test.error} />
            </div>
          </div>
          <div className="grid min-h-0 gap-3 md:grid-cols-[180px_220px_minmax(0,1fr)]">
            <div className="rounded border border-border">
              <div className="border-b border-border px-2 py-1 text-2xs font-semibold tracking-wider text-fg-subtle uppercase">Schemas</div>
              {schemas.isLoading ? (
                <LoadingState rows={3} className="px-2" />
              ) : schemas.error ? (
                <ErrorState error={schemas.error} compact onRetry={() => void schemas.refetch()} />
              ) : (
                <ul className="max-h-72 overflow-auto scrollbar-thin">
                  {(schemas.data ?? []).map((s) => (
                    <li key={s.name}>
                      <button
                        type="button"
                        onClick={() => {
                          setSchema(s.name);
                          setTable(null);
                        }}
                        className={cn("flex w-full items-center justify-between px-2 py-1 text-left text-sm hover:bg-bg-muted", schema === s.name && "bg-accent-soft")}
                      >
                        <span className="truncate">{s.name}</span>
                        {s.table_count != null ? <span className="text-2xs text-fg-subtle">{s.table_count}</span> : null}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="rounded border border-border">
              <div className="border-b border-border px-2 py-1 text-2xs font-semibold tracking-wider text-fg-subtle uppercase">Tables</div>
              {!schema ? (
                <p className="px-2 py-3 text-xs text-fg-subtle">Choose a schema.</p>
              ) : tables.isLoading ? (
                <LoadingState rows={4} className="px-2" />
              ) : tables.error ? (
                <ErrorState error={tables.error} compact onRetry={() => void tables.refetch()} />
              ) : !tables.data?.length ? (
                <p className="px-2 py-3 text-xs text-fg-subtle">No tables in this schema.</p>
              ) : (
                <ul className="max-h-72 overflow-auto scrollbar-thin">
                  {tables.data.map((t) => (
                    <li key={t.name}>
                      <button
                        type="button"
                        onClick={() => setTable(t.name)}
                        className={cn("flex w-full items-center justify-between gap-2 px-2 py-1 text-left text-sm hover:bg-bg-muted", table === t.name && "bg-accent-soft")}
                      >
                        <span className="truncate">{t.name}</span>
                        <span className="shrink-0 text-2xs text-fg-subtle tabular">
                          {t.kind === "view" ? "view" : t.row_count != null ? formatInt(t.row_count) : ""}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="flex min-w-0 flex-col gap-2">
              {!table ? (
                <EmptyState compact icon={Database} title="Pick a table to preview" description="Previews run a read-only LIMIT query." />
              ) : preview.isLoading ? (
                <LoadingState variant="block" label="Loading preview" />
              ) : preview.error ? (
                <ErrorState error={preview.error} compact onRetry={() => void preview.refetch()} />
              ) : preview.data ? (
                <DataGrid columns={preview.data.columns} rows={preview.data.rows} height={260} className="rounded border border-border" />
              ) : null}
              {table ? (
                <div className="flex flex-wrap items-end gap-2">
                  <Field label="Workspace table name" htmlFor="snap-name" error={!TABLE_NAME_RE.test(targetName) ? "Lowercase letters, digits and _" : undefined}>
                    <Input id="snap-name" className="w-56 font-mono" value={targetName} onChange={(e) => setTargetName(e.target.value)} />
                  </Field>
                  <Field label="Row limit (optional)" htmlFor="snap-limit">
                    <Input id="snap-limit" className="w-32" inputMode="numeric" value={rowLimit} onChange={(e) => setRowLimit(e.target.value)} placeholder="all rows" />
                  </Field>
                  <Button
                    variant="primary"
                    onClick={() => snapshot.mutate()}
                    disabled={snapshot.isPending || !TABLE_NAME_RE.test(targetName)}
                  >
                    {snapshot.isPending ? <Spinner className="text-accent-fg" /> : null} Snapshot into workspace
                  </Button>
                </div>
              ) : null}
              <JobProgress progress={progress} />
              <InlineError error={snapshot.error} />
              {created ? (
                <p className="text-sm text-positive">
                  Snapshot ready.{" "}
                  <Link className="text-accent underline" href={href(`/data/${created}`)} onClick={() => onOpenChange(false)}>
                    Open dataset
                  </Link>
                </p>
              ) : null}
            </div>
          </div>
        </DialogBody>
        <DialogFooter>
          <Badge tone="outline" className="mr-auto">
            Read-only access
          </Badge>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
