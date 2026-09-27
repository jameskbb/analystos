"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Calendar, Plus } from "lucide-react";
import { dimensions as dimensionsApi, semantic } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import type { Dimension } from "@/lib/api/types";
import type { DashboardFilter } from "@/lib/api/resources/outputs";
import { useWorkspace } from "@/components/providers/workspace";
import { FilterChips } from "@/components/analysis/filter-chips";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import { InlineError } from "@/components/states/states";
import { formatWindow, humanize } from "@/lib/format";
import { DATE_PRESETS, type DatePreset, presetRange } from "./model";

export function useDimensions() {
  const { id: ws } = useWorkspace();
  return useQuery({ queryKey: qk.dimensions(ws), queryFn: () => dimensionsApi.list(ws) });
}

/** Date control: presets resolved by the workspace calendar, or an explicit range. */
export function DateRangeControl({
  preset,
  custom,
  onChange,
}: {
  preset: DatePreset;
  custom: { start: string; end: string } | null;
  onChange: (preset: DatePreset, custom: { start: string; end: string } | null) => void;
}) {
  const { id: ws } = useWorkspace();
  const text = DATE_PRESETS.find((p) => p.value === preset)?.text ?? null;
  const resolved = useQuery({
    queryKey: ["ws", ws, "resolve-period", text],
    queryFn: () => semantic.resolvePeriod(ws, text!),
    enabled: !!text,
    staleTime: 5 * 60_000,
    retry: false,
  });
  const window = (resolved.data as { window?: { start: string; end: string } } | undefined)?.window;
  const fallback = presetRange(preset, new Date());
  const shown = preset === "custom" ? custom : window ?? fallback;
  const id = React.useId();
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Calendar className="size-3.5 text-fg-subtle" aria-hidden />
      <label htmlFor={`${id}-preset`} className="sr-only">
        Date range
      </label>
      <NativeSelect
        id={`${id}-preset`}
        className="h-7 w-40"
        value={preset}
        onChange={(e) => {
          const p = e.target.value as DatePreset;
          onChange(p, p === "custom" ? custom ?? presetRange("last_month", new Date()) : null);
        }}
      >
        {DATE_PRESETS.map((p) => (
          <option key={p.value} value={p.value}>
            {p.label}
          </option>
        ))}
      </NativeSelect>
      {preset === "custom" ? (
        <>
          <Input
            type="date"
            aria-label="Start date"
            className="h-7 w-36"
            value={custom?.start ?? ""}
            onChange={(e) => onChange("custom", { start: e.target.value, end: custom?.end ?? e.target.value })}
          />
          <span className="text-fg-subtle">–</span>
          <Input
            type="date"
            aria-label="End date"
            className="h-7 w-36"
            value={custom?.end ?? ""}
            onChange={(e) => onChange("custom", { start: custom?.start ?? e.target.value, end: e.target.value })}
          />
        </>
      ) : shown ? (
        <span className="text-xs text-fg-subtle tabular" title={window ? "Resolved by the workspace calendar" : "Calendar-month interpretation"}>
          {formatWindow(shown.start, shown.end)}
        </span>
      ) : null}
    </div>
  );
}

function AddFilterPopover({ onAdd, exclude }: { onAdd: (f: DashboardFilter) => void; exclude: string[] }) {
  const { id: ws } = useWorkspace();
  const dims = useDimensions();
  const [open, setOpen] = React.useState(false);
  const [dimension, setDimension] = React.useState("");
  const [q, setQ] = React.useState("");
  const [selected, setSelected] = React.useState<string[]>([]);
  const candidates = (dims.data ?? []).filter((d: Dimension) => d.type !== "time" && !exclude.includes(d.name));
  const values = useQuery({
    queryKey: qk.dimensionValues(ws, dimension, q),
    queryFn: () => dimensionsApi.values(ws, dimension, { q: q || undefined, limit: 100 }),
    enabled: !!dimension,
  });
  const reset = () => {
    setDimension("");
    setQ("");
    setSelected([]);
  };
  const dimLabel = candidates.find((d) => d.name === dimension)?.label || humanize(dimension);
  return (
    <Popover
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <PopoverTrigger asChild>
        <Button variant="ghost" size="xs" aria-label="Add dashboard filter">
          <Plus /> Filter
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72" align="start">
        <div className="flex flex-col gap-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor="dash-filter-dim">Dimension</Label>
            <NativeSelect
              id="dash-filter-dim"
              value={dimension}
              onChange={(e) => {
                setDimension(e.target.value);
                setSelected([]);
              }}
            >
              <option value="">Choose a dimension…</option>
              {candidates.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.label || humanize(d.name)}
                </option>
              ))}
            </NativeSelect>
            {dims.isError ? <InlineError error={dims.error} /> : null}
          </div>
          {dimension ? (
            <>
              <Input placeholder="Search values" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search values" />
              <div className="max-h-52 overflow-auto rounded border border-border scrollbar-thin" role="group" aria-label={`${dimLabel} values`}>
                {values.isLoading ? (
                  <div className="flex items-center gap-2 p-2 text-xs text-fg-subtle">
                    <Spinner /> Loading values
                  </div>
                ) : values.isError ? (
                  <InlineError error={values.error} className="m-1" />
                ) : (values.data?.values ?? []).length === 0 ? (
                  <p className="p-2 text-xs text-fg-subtle">No values.</p>
                ) : (
                  (values.data?.values ?? []).map((v, i) => {
                    const s = v === null ? "" : String(v);
                    const checked = selected.includes(s);
                    return (
                      <label key={`${s}-${i}`} className="flex h-7 cursor-pointer items-center gap-2 px-2 text-sm hover:bg-bg-muted">
                        <Checkbox
                          checked={checked}
                          onCheckedChange={(c) => setSelected((prev) => (c ? [...prev, s] : prev.filter((x) => x !== s)))}
                        />
                        <span className="min-w-0 flex-1 truncate">{s || "(blank)"}</span>
                        {values.data?.counts?.[i] !== undefined ? (
                          <span className="text-2xs text-fg-faint tabular">{values.data.counts[i].toLocaleString()}</span>
                        ) : null}
                      </label>
                    );
                  })
                )}
              </div>
            </>
          ) : null}
          <div className="flex justify-end gap-1.5">
            <Button size="xs" variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              size="xs"
              variant="primary"
              disabled={!dimension || !selected.length}
              onClick={() => {
                onAdd({ dimension, op: "in", values: selected, label: dimLabel });
                setOpen(false);
                reset();
              }}
            >
              Apply
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

/** Dashboard filter bar: filters and dates apply to every semantic tile. */
export function DashboardFilterBar({
  filters,
  onFiltersChange,
  preset,
  custom,
  onDateChange,
  trailing,
}: {
  filters: DashboardFilter[];
  onFiltersChange: (f: DashboardFilter[]) => void;
  preset: DatePreset;
  custom: { start: string; end: string } | null;
  onDateChange: (p: DatePreset, c: { start: string; end: string } | null) => void;
  trailing?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border bg-bg-subtle px-4 py-1.5 no-print sm:px-5">
      <DateRangeControl preset={preset} custom={custom} onChange={onDateChange} />
      <span className="h-4 w-px bg-border" aria-hidden />
      <FilterChips
        items={filters}
        emptyLabel="No dashboard filters"
        onRemove={(i) => onFiltersChange(filters.filter((_, j) => j !== i))}
      />
      <AddFilterPopover onAdd={(f) => onFiltersChange([...filters, f])} exclude={filters.map((f) => f.dimension)} />
      {trailing ? <div className="ml-auto flex items-center gap-1.5">{trailing}</div> : null}
    </div>
  );
}
