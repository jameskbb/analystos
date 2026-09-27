"use client";
import * as React from "react";
import { AlertTriangle, EyeOff, FileSpreadsheet, Table2 } from "lucide-react";
import type { DetectedTable, FileInspectionResult, SheetInfo } from "@/lib/api/resources/data";
import { DataGrid } from "@/components/data-grid/data-grid";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { SectionLabel } from "@/components/shell/page";
import { formatInt } from "@/lib/format";
import { cn } from "@/lib/utils";

const TYPE_TO_SQL: Record<string, string> = {
  integer: "BIGINT",
  float: "DOUBLE",
  boolean: "BOOLEAN",
  date: "DATE",
  timestamp: "TIMESTAMP",
  string: "VARCHAR",
};

const SKIP_LABEL: Record<string, string> = {
  title: "Title row",
  blank: "Empty row",
  total: "Totals row",
  note: "Note row",
  malformed: "Malformed row",
};

/** Groups detected tables by sheet, keeping sheets that have no table (empty or unreadable) visible. */
export function groupBySheet(inspection: FileInspectionResult): { sheet: SheetInfo | null; tables: DetectedTable[] }[] {
  if (!inspection.sheets?.length) return [{ sheet: null, tables: inspection.tables }];
  return inspection.sheets.map((s) => ({ sheet: s, tables: inspection.tables.filter((t) => t.sheet === s.name) }));
}

/**
 * Excel sheet picker (spec §9): lists sheets with their detected tables, marks hidden/empty sheets and
 * lets the analyst choose which tables to ingest. Focusing a table shows its preview.
 */
export function SheetPicker({
  inspection,
  focusedKey,
  onFocus,
  selected,
  onToggle,
}: {
  inspection: FileInspectionResult;
  focusedKey: string | null;
  onFocus: (key: string) => void;
  selected: Set<string>;
  onToggle: (key: string, on: boolean) => void;
}) {
  const groups = groupBySheet(inspection);
  return (
    <div className="flex flex-col gap-2" role="group" aria-label="Sheets and detected tables">
      {groups.map(({ sheet, tables }) => (
        <div key={sheet?.name ?? "file"} className="rounded border border-border">
          {sheet ? (
            <div className="flex items-center gap-1.5 border-b border-border bg-bg-subtle px-2 py-1 text-xs">
              <FileSpreadsheet className="size-3.5 text-fg-subtle" aria-hidden />
              <span className="truncate font-medium">{sheet.name}</span>
              {sheet.dimensions ? <span className="font-mono text-2xs text-fg-faint">{sheet.dimensions}</span> : null}
              <span className="ml-auto flex gap-1">
                {sheet.visible === false ? (
                  <Badge tone="outline">
                    <EyeOff /> Hidden
                  </Badge>
                ) : null}
                {sheet.empty ? <Badge tone="neutral">Empty</Badge> : null}
              </span>
            </div>
          ) : null}
          {tables.length === 0 ? (
            <div className="px-2 py-2 text-xs text-fg-subtle">
              {sheet?.empty ? "No data on this sheet." : "No table detected on this sheet."}
            </div>
          ) : (
            <ul>
              {tables.map((t) => (
                <li
                  key={t.key}
                  className={cn(
                    "flex items-center gap-2 border-b border-border px-2 py-1.5 last:border-b-0",
                    focusedKey === t.key && "bg-accent-soft",
                  )}
                >
                  <Checkbox
                    checked={selected.has(t.key)}
                    onCheckedChange={(v) => onToggle(t.key, v === true)}
                    aria-label={`Ingest ${t.title || t.name_suggestion}`}
                  />
                  <button
                    type="button"
                    onClick={() => onFocus(t.key)}
                    aria-pressed={focusedKey === t.key}
                    className="flex min-w-0 flex-1 flex-col text-left"
                  >
                    <span className="flex items-center gap-1.5 text-sm">
                      <Table2 className="size-3.5 shrink-0 text-fg-subtle" aria-hidden />
                      <span className="truncate font-medium">{t.title || t.name_suggestion}</span>
                    </span>
                    <span className="truncate text-2xs text-fg-subtle tabular">
                      {t.range ? `${t.range} · ` : ""}
                      {formatInt(t.row_count)} rows · {t.columns.length} cols
                      {t.header_row ? ` · header row ${t.header_row}` : " · no header"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

/** Details of one detected table: header/title/skipped rows, column types with notes, and a preview grid. */
export function DetectedTablePreview({ table }: { table: DetectedTable }) {
  const columns = table.columns.map((c) => ({ name: c.name, type: TYPE_TO_SQL[c.inferred_type] ?? c.inferred_type }));
  const skipped = table.skipped_rows ?? [];
  const renamed = table.columns.filter((c) => c.original_name && c.original_name !== c.name);
  const flagged = table.columns.filter((c) => (c.nonconforming_count ?? 0) > 0 || (c.notes?.length ?? 0) > 0);
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-fg-subtle">Header row</dt>
          <dd className="font-medium tabular">{table.header_row ?? "None detected"}</dd>
        </div>
        <div>
          <dt className="text-fg-subtle">Data rows</dt>
          <dd className="font-medium tabular">
            {table.first_data_row}–{table.last_data_row} ({formatInt(table.row_count)})
          </dd>
        </div>
        <div>
          <dt className="text-fg-subtle">Range</dt>
          <dd className="font-mono">{table.range ?? "Whole file"}</dd>
        </div>
        <div>
          <dt className="text-fg-subtle">Title</dt>
          <dd className="truncate" title={table.title ?? undefined}>
            {table.title ?? "None"}
          </dd>
        </div>
      </dl>

      {skipped.length || table.notes?.length ? (
        <div className="rounded border border-border bg-bg-subtle px-2.5 py-2 text-xs">
          <SectionLabel className="mb-1">Detected and excluded from the data</SectionLabel>
          <ul className="flex flex-col gap-0.5">
            {skipped.slice(0, 8).map((s) => (
              <li key={`${s.row}-${s.reason}`} className="flex gap-2">
                <span className="w-24 shrink-0 text-fg-subtle">
                  {SKIP_LABEL[s.reason] ?? s.reason} {s.row}
                </span>
                <span className="truncate font-mono text-fg-muted">{s.content || "(empty)"}</span>
              </li>
            ))}
            {skipped.length > 8 ? <li className="text-fg-subtle">+{skipped.length - 8} more rows</li> : null}
            {(table.notes ?? []).map((n, i) => (
              <li key={`n${i}`} className="text-fg-muted">
                {n}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div>
        <SectionLabel className="mb-1">Columns and inferred types</SectionLabel>
        <div className="flex flex-wrap gap-1">
          {table.columns.map((c) => (
            <span
              key={c.name}
              className="inline-flex h-5 items-center gap-1 rounded-sm border border-border bg-bg px-1.5 text-xs"
              title={c.original_name !== c.name ? `Original header: ${c.original_name}` : undefined}
            >
              <span className="font-medium">{c.name}</span>
              <span className="font-mono text-2xs text-fg-subtle">{c.inferred_type}</span>
            </span>
          ))}
        </div>
        {renamed.length ? (
          <p className="mt-1 text-2xs text-fg-subtle">
            {renamed.length} header{renamed.length === 1 ? "" : "s"} normalized to SQL-safe names (hover a column for the
            original).
          </p>
        ) : null}
      </div>

      {flagged.length ? (
        <ul className="flex flex-col gap-1 text-xs" aria-label="Column warnings">
          {flagged.map((c) => (
            <li key={c.name} className="flex gap-1.5 text-warning">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              <span>
                <span className="font-medium">{c.name}</span>:{" "}
                {(c.nonconforming_count ?? 0) > 0
                  ? `${formatInt(c.nonconforming_count ?? 0)} value(s) don't fit ${c.inferred_type}${
                      c.nonconforming_examples?.length ? ` (e.g. ${c.nonconforming_examples.slice(0, 3).join(", ")})` : ""
                    }${c.candidate_type ? `; looks like ${c.candidate_type}` : ""}. `
                  : ""}
                {(c.notes ?? []).join(" ")}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      <div>
        <SectionLabel className="mb-1">
          Preview (first {formatInt(table.preview_rows.length)} of {formatInt(table.row_count)} rows)
        </SectionLabel>
        <DataGrid columns={columns} rows={table.preview_rows} height={240} className="rounded border border-border" ariaLabel="File preview" />
      </div>
    </div>
  );
}
