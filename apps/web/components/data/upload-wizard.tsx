"use client";
import * as React from "react";
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileUp, RotateCcw, UploadCloud } from "lucide-react";
import { toast } from "sonner";
import { datasets, type DetectedTable, type FileInspectionResult, type UploadRecord } from "@/lib/api/resources/data";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { InlineError } from "@/components/states/states";
import { Spinner } from "@/components/ui/spinner";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";
import { DetectedTablePreview, SheetPicker } from "./sheet-picker";
import { JobProgress, useJobRunner } from "./use-job";
import { TABLE_NAME_RE, datasetTitle, suggestTableName } from "./naming";

export const ACCEPTED_FILES = ".csv,.tsv,.txt,.xlsx,.xlsm,.parquet,.json,.ndjson,.jsonl";

type Target = { table_name: string; dataset_name: string };

function defaultTarget(t: DetectedTable, file: string, multi: boolean): Target {
  const table = multi ? suggestTableName(t.name_suggestion) : suggestTableName(t.name_suggestion || file);
  return { table_name: table, dataset_name: datasetTitle(table) };
}

/**
 * Upload wizard (spec §8-9): upload → inspect (dialect, sheets, header rows, title/blank rows, table blocks,
 * inferred types, preview) → optional header-row override → choose tables and names → ingest with progress.
 * The source file is never modified; ingest writes a new table in the workspace store.
 */
export function UploadWizard({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { id: ws, href } = useWorkspace();
  const qc = useQueryClient();
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = React.useState(false);
  const [upload, setUpload] = React.useState<UploadRecord | null>(null);
  const [inspection, setInspection] = React.useState<FileInspectionResult | null>(null);
  const [focused, setFocused] = React.useState<string | null>(null);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [targets, setTargets] = React.useState<Record<string, Target>>({});
  const [headerOverride, setHeaderOverride] = React.useState("");
  /** Header rows the analyst confirmed via "Re-read", keyed by the table keys that preview produced. */
  const [appliedHeaders, setAppliedHeaders] = React.useState<Record<string, number>>({});
  const [delimiter, setDelimiter] = React.useState("");
  const [ifExists, setIfExists] = React.useState<"fail" | "replace" | "append">("fail");
  const [done, setDone] = React.useState<{ dataset_id: string; table_name: string }[]>([]);
  const [ingestError, setIngestError] = React.useState<unknown>(null);
  const [ingesting, setIngesting] = React.useState(false);
  const { run, progress } = useJobRunner();

  const reset = () => {
    setUpload(null);
    setInspection(null);
    setFocused(null);
    setSelected(new Set());
    setTargets({});
    setHeaderOverride("");
    setAppliedHeaders({});
    setDelimiter("");
    setIfExists("fail");
    setDone([]);
    setIngestError(null);
  };

  const adopt = (insp: FileInspectionResult, file: string, keepSelection = false) => {
    setInspection(insp);
    const multi = insp.tables.length > 1;
    setTargets((prev) => {
      const next = { ...prev };
      insp.tables.forEach((t) => {
        if (!next[t.key]) next[t.key] = defaultTarget(t, file, multi);
      });
      return next;
    });
    if (!keepSelection) setSelected(new Set(insp.tables.slice(0, 1).map((t) => t.key)));
    setFocused((f) => (f && insp.tables.some((t) => t.key === f) ? f : insp.tables[0]?.key ?? null));
  };

  const uploadMut = useMutation({
    mutationFn: (file: File) => datasets.upload(ws, file),
    onSuccess: (rec) => {
      setUpload(rec);
      adopt(rec.inspection, rec.filename);
    },
  });

  const focusedTable = inspection?.tables.find((t) => t.key === focused) ?? null;

  const redetect = useMutation({
    mutationFn: async () => {
      if (!upload || !focusedTable) throw new Error("Nothing to preview");
      const header = headerOverride.trim() ? Number(headerOverride) : null;
      if (header !== null && (!Number.isInteger(header) || header < 1)) throw new Error("Header row must be a whole number ≥ 1.");
      return datasets.previewUpload(ws, upload.id, {
        sheet: focusedTable.sheet ?? null,
        header_row: header,
        delimiter: delimiter || null,
      });
    },
    onSuccess: (res) => {
      if (!inspection || !upload) return;
      const sheet = focusedTable?.sheet ?? null;
      const incoming = res.preview.tables.filter((t) => (sheet ? t.sheet === sheet : true));
      const kept = inspection.tables.filter((t) => (sheet ? t.sheet !== sheet : false));
      const merged: FileInspectionResult = {
        ...inspection,
        dialect: res.preview.dialect ?? inspection.dialect,
        tables: [...kept, ...incoming],
        warnings: res.preview.warnings ?? inspection.warnings,
      };
      adopt(merged, upload.filename, true);
      setSelected((prev) => {
        const next = new Set([...prev].filter((k) => merged.tables.some((t) => t.key === k)));
        if (incoming[0]) next.add(incoming[0].key);
        return next;
      });
      if (incoming[0]) setFocused(incoming[0].key);
      const header = headerOverride.trim() ? Number(headerOverride) : null;
      setAppliedHeaders((prev) => {
        const next = { ...prev };
        incoming.forEach((t) => {
          // Ingest with the header row the preview actually used, so preview == ingest.
          const used = t.header_row ?? header;
          if (header !== null && used != null) next[t.key] = used;
          else delete next[t.key];
        });
        return next;
      });
      // What you preview is what gets ingested: if the server read a different header row than the
      // one requested, say so instead of silently showing another layout.
      const ignored = header !== null ? incoming.filter((t) => t.header_row != null && t.header_row !== header) : [];
      if (ignored.length)
        toast.warning(`The preview used header row ${ignored[0].header_row}, not ${header}`, {
          description: "Check the sheet and row number; ingestion uses the header row shown in this preview.",
        });
      else toast.success("Preview updated with your settings");
    },
  });

  const chosen = inspection?.tables.filter((t) => selected.has(t.key)) ?? [];
  const nameErrors = chosen
    .map((t) => ({ t, name: targets[t.key]?.table_name ?? "" }))
    .filter(({ name }) => !TABLE_NAME_RE.test(name));
  const dupNames = new Set(
    chosen.map((t) => targets[t.key]?.table_name).filter((n, i, arr) => n && arr.indexOf(n) !== i),
  );

  const ingest = async () => {
    if (!upload) return;
    setIngestError(null);
    setIngesting(true);
    const created: { dataset_id: string; table_name: string }[] = [];
    try {
      for (const t of chosen) {
        const target = targets[t.key];
        const header = appliedHeaders[t.key] ?? null;
        const job = await run(() =>
          datasets.ingest(ws, upload.id, {
            dataset_name: target.dataset_name || null,
            table_name: target.table_name,
            if_exists: ifExists,
            options: {
              sheet: t.sheet ?? null,
              table_key: t.key,
              header_row: header,
              delimiter: delimiter || null,
            },
          }),
        );
        const result = job.result ?? {};
        created.push({
          dataset_id: String(result.dataset_id ?? job.resource_id ?? ""),
          table_name: String(result.table_name ?? target.table_name),
        });
      }
      setDone(created);
      await qc.invalidateQueries({ queryKey: qk.datasets(ws) });
      await qc.invalidateQueries({ queryKey: ["ws", ws, "relationships"] });
      toast.success(`Ingested ${created.length} table${created.length === 1 ? "" : "s"}`);
    } catch (e) {
      setIngestError(e);
      if (created.length) {
        setDone(created);
        void qc.invalidateQueries({ queryKey: qk.datasets(ws) });
      }
    } finally {
      setIngesting(false);
    }
  };

  const pickFile = (files: FileList | null) => {
    const f = files?.[0];
    if (f) uploadMut.mutate(f);
  };

  const isExcel = inspection?.format === "excel";
  const step: "pick" | "review" | "done" = done.length && !ingestError ? "done" : inspection ? "review" : "pick";

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o && (uploadMut.isPending || ingesting)) return;
        onOpenChange(o);
        if (!o) reset();
      }}
    >
      <DialogContent size="xl" className="max-h-[88vh]">
        <DialogHeader>
          <DialogTitle>Upload a file</DialogTitle>
          <DialogDescription>
            CSV, Excel, Parquet or JSON. You review how AnalystOS reads the file before anything is ingested; the source
            file is never modified.
          </DialogDescription>
        </DialogHeader>

        {step === "pick" ? (
          <DialogBody>
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                pickFile(e.dataTransfer.files);
              }}
              className={cn(
                "flex flex-col items-center justify-center gap-2 rounded-md border border-dashed border-border-strong px-6 py-12 text-center",
                dragging && "border-accent bg-accent-soft",
              )}
            >
              {uploadMut.isPending ? (
                <>
                  <Spinner className="size-5" />
                  <div className="text-sm">Uploading and inspecting…</div>
                </>
              ) : (
                <>
                  <UploadCloud className="size-6 text-fg-subtle" aria-hidden />
                  <div className="text-sm font-medium">Drop a file here</div>
                  <div className="text-xs text-fg-subtle">.csv .tsv .xlsx .parquet .json .ndjson</div>
                  <Button variant="primary" onClick={() => inputRef.current?.click()} className="mt-1">
                    <FileUp /> Choose file
                  </Button>
                </>
              )}
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPTED_FILES}
                className="sr-only"
                aria-label="Choose a file to upload"
                onChange={(e) => pickFile(e.target.files)}
              />
            </div>
            <InlineError error={uploadMut.error} className="mt-3" />
          </DialogBody>
        ) : null}

        {step === "review" && inspection && upload ? (
          <>
            <DialogBody className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                <span className="font-medium">{upload.filename}</span>
                <Badge tone="outline">{inspection.format.toUpperCase()}</Badge>
                <span className="text-fg-subtle">{formatBytes(upload.size_bytes)}</span>
                {inspection.encoding ? <span className="text-fg-subtle">Encoding {inspection.encoding}</span> : null}
                {inspection.dialect ? (
                  <span className="text-fg-subtle">
                    Delimiter <code className="font-mono">{JSON.stringify(inspection.dialect.delimiter)}</code>
                    {inspection.dialect.has_bom ? " · BOM" : ""}
                  </span>
                ) : null}
                <span className="font-mono text-2xs text-fg-faint" title="SHA-256 of the stored file">
                  sha256 {upload.sha256.slice(0, 12)}
                </span>
              </div>
              {inspection.warnings?.length ? (
                <ul className="rounded border border-warning/30 bg-warning-soft px-2.5 py-1.5 text-xs text-warning">
                  {inspection.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              ) : null}
              {inspection.tables.length === 0 ? (
                <InlineError error="No table could be detected in this file. Try a different header row or check the file." />
              ) : null}

              <div className="grid min-h-0 gap-3 md:grid-cols-[260px_minmax(0,1fr)]">
                <div className="flex flex-col gap-3">
                  <SheetPicker
                    inspection={inspection}
                    focusedKey={focused}
                    onFocus={setFocused}
                    selected={selected}
                    onToggle={(k, on) =>
                      setSelected((prev) => {
                        const next = new Set(prev);
                        if (on) next.add(k);
                        else next.delete(k);
                        return next;
                      })
                    }
                  />
                  <div className="rounded border border-border p-2">
                    <div className="mb-1.5 text-xs font-medium">Override detection</div>
                    <div className="flex flex-col gap-2">
                      <Field
                        label="Header row (1-based)"
                        htmlFor="hdr"
                        hint={isExcel ? "Applies to the focused sheet." : undefined}
                      >
                        <Input
                          id="hdr"
                          inputMode="numeric"
                          value={headerOverride}
                          placeholder={focusedTable?.header_row ? String(focusedTable.header_row) : "auto"}
                          onChange={(e) => setHeaderOverride(e.target.value)}
                        />
                      </Field>
                      {!isExcel && inspection.format === "csv" ? (
                        <Field label="Delimiter" htmlFor="delim">
                          <NativeSelect id="delim" value={delimiter} onChange={(e) => setDelimiter(e.target.value)}>
                            <option value="">Auto ({JSON.stringify(inspection.dialect?.delimiter ?? ",")})</option>
                            <option value=",">Comma</option>
                            <option value=";">Semicolon</option>
                            <option value={"\t"}>Tab</option>
                            <option value="|">Pipe</option>
                          </NativeSelect>
                        </Field>
                      ) : null}
                      <Button size="xs" onClick={() => redetect.mutate()} disabled={redetect.isPending || !focusedTable}>
                        {redetect.isPending ? <Spinner /> : <RotateCcw />} Re-read with these settings
                      </Button>
                      <InlineError error={redetect.error} />
                    </div>
                  </div>
                </div>
                <div className="min-w-0">
                  {focusedTable ? <DetectedTablePreview table={focusedTable} /> : null}
                </div>
              </div>

              {chosen.length ? (
                <div className="rounded border border-border">
                  <div className="border-b border-border bg-bg-subtle px-2.5 py-1.5 text-xs font-medium">
                    Ingest {chosen.length} table{chosen.length === 1 ? "" : "s"} into the workspace store
                  </div>
                  <div className="flex flex-col gap-2 p-2.5">
                    {chosen.map((t) => {
                      const target = targets[t.key];
                      const bad = nameErrors.some((n) => n.t.key === t.key) || dupNames.has(target?.table_name);
                      return (
                        <div key={t.key} className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)]">
                          <div className="self-center truncate text-xs text-fg-subtle">
                            {t.sheet ? `${t.sheet} · ` : ""}
                            {t.title || t.name_suggestion}
                          </div>
                          <Field
                            label="Table name"
                            htmlFor={`tn-${t.key}`}
                            error={
                              bad
                                ? dupNames.has(target?.table_name)
                                  ? "Duplicate name"
                                  : "Lowercase letters, digits and _ (max 63)"
                                : undefined
                            }
                          >
                            <Input
                              id={`tn-${t.key}`}
                              className="font-mono"
                              value={target?.table_name ?? ""}
                              onChange={(e) =>
                                setTargets((p) => ({ ...p, [t.key]: { ...p[t.key], table_name: e.target.value } }))
                              }
                            />
                          </Field>
                          <Field label="Dataset name" htmlFor={`dn-${t.key}`}>
                            <Input
                              id={`dn-${t.key}`}
                              value={target?.dataset_name ?? ""}
                              onChange={(e) =>
                                setTargets((p) => ({ ...p, [t.key]: { ...p[t.key], dataset_name: e.target.value } }))
                              }
                            />
                          </Field>
                        </div>
                      );
                    })}
                    <Field label="If the table already exists" htmlFor="ifx" className="max-w-xs">
                      <NativeSelect id="ifx" value={ifExists} onChange={(e) => setIfExists(e.target.value as typeof ifExists)}>
                        <option value="fail">Stop (don&apos;t overwrite)</option>
                        <option value="replace">Replace it (new dataset version)</option>
                        <option value="append">Append rows (new dataset version)</option>
                      </NativeSelect>
                    </Field>
                  </div>
                </div>
              ) : null}
              <JobProgress progress={progress} />
              <InlineError error={ingestError} />
            </DialogBody>
            <DialogFooter>
              <Button variant="ghost" onClick={reset} disabled={ingesting} className="mr-auto">
                Choose a different file
              </Button>
              <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={ingesting}>
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={() => void ingest()}
                disabled={ingesting || chosen.length === 0 || nameErrors.length > 0 || dupNames.size > 0}
              >
                {ingesting ? <Spinner className="text-accent-fg" /> : null}
                Ingest {chosen.length > 1 ? `${chosen.length} tables` : "table"}
              </Button>
            </DialogFooter>
          </>
        ) : null}

        {step === "done" ? (
          <>
            <DialogBody className="flex flex-col items-center gap-2 py-8 text-center">
              <CheckCircle2 className="size-6 text-positive" aria-hidden />
              <div className="text-sm font-medium">Ingest complete</div>
              <p className="max-w-md text-sm text-fg-subtle">
                AnalystOS is profiling the new data and refreshing relationship suggestions. Review them before relying on
                joins.
              </p>
              <ul className="mt-2 flex flex-col gap-1">
                {done.map((d) => (
                  <li key={d.table_name}>
                    {d.dataset_id ? (
                      <Link
                        href={href(`/data/${d.dataset_id}`)}
                        onClick={() => onOpenChange(false)}
                        className="font-mono text-sm text-accent hover:underline"
                      >
                        {d.table_name}
                      </Link>
                    ) : (
                      <span className="font-mono text-sm">{d.table_name}</span>
                    )}
                  </li>
                ))}
              </ul>
            </DialogBody>
            <DialogFooter>
              <Button variant="secondary" onClick={reset}>
                Upload another file
              </Button>
              <Button variant="primary" onClick={() => onOpenChange(false)}>
                Done
              </Button>
            </DialogFooter>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
