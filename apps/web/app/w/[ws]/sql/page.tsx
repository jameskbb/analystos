"use client";
import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Group, Panel } from "react-resizable-panels";
import { toast } from "sonner";
import { Bookmark, Database, FilePlus2, History, Play, Save, Upload, Wand2 } from "lucide-react";
import {
  queries,
  type QueryParameterDef,
  type QueryRunOut,
  type QueryRunResultOut,
  type SavedQueryOut,
} from "@/lib/api/resources/workbench";
import { qk } from "@/lib/api/keys";
import type { ChartConfig } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { CodeEditor, type CodeEditorHandle, type SchemaTable } from "@/components/code/code-editor";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Tooltip } from "@/components/ui/tooltip";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/states";
import { LoadDemoButton } from "@/components/shell/use-load-demo";
import { ResizeHandle } from "@/components/workbench/resize-handle";
import { SchemaBrowser } from "@/components/workbench/schema-browser";
import { ParamBar } from "@/components/workbench/param-bar";
import { ResultsPanel } from "@/components/workbench/results-panel";
import { QuerySidePanel } from "@/components/workbench/query-side-panel";
import { SaveQueryDialog, type SaveQueryValues } from "@/components/workbench/save-query-dialog";
import { GenerateSqlDialog } from "@/components/workbench/generate-sql-dialog";
import { useMediaQuery } from "@/components/workbench/use-media";
import {
  coerceParamValue,
  detectParams,
  normalizeParams,
  paramValueToText,
  syncParamDefs,
  validateParamValues,
} from "@/components/workbench/sql-params";
import { sqlDraftKey as draftKey } from "@/components/workbench/sql-draft";
import { cn, isMac } from "@/lib/utils";

const LIMITS = [1000, 10000, 100000];

function SqlWorkspace() {
  const { id: ws, href, canEdit } = useWorkspace();
  const router = useRouter();
  const search = useSearchParams();
  const qc = useQueryClient();
  const editor = React.useRef<CodeEditorHandle>(null);
  const wide = useMediaQuery("(min-width: 1024px)");
  const medium = useMediaQuery("(min-width: 768px)");

  const schemaQ = useQuery({ queryKey: qk.schema(ws), queryFn: () => queries.schema(ws) });

  const [sql, setSql] = React.useState("");
  const [defs, setDefs] = React.useState<QueryParameterDef[]>([]);
  const [values, setValues] = React.useState<Record<string, string>>({});
  const [saved, setSaved] = React.useState<SavedQueryOut | null>(null);
  const [run, setRun] = React.useState<QueryRunResultOut | null>(null);
  const [runError, setRunError] = React.useState<unknown>(null);
  const [chart, setChart] = React.useState<ChartConfig | null>(null);
  const [limit, setLimit] = React.useState(10000);
  const [side, setSide] = React.useState<null | "history" | "saved">(null);
  const [schemaSheet, setSchemaSheet] = React.useState(false);
  const [saveOpen, setSaveOpen] = React.useState(false);
  const [genOpen, setGenOpen] = React.useState(false);
  const [paramErrors, setParamErrors] = React.useState<Record<string, string>>({});
  const [mac, setMac] = React.useState(true);
  const loadedDraft = React.useRef(false);

  React.useEffect(() => setMac(isMac()), []);

  // Restore the unsaved draft once (unless a saved query or run is being opened).
  React.useEffect(() => {
    if (loadedDraft.current) return;
    loadedDraft.current = true;
    if (search.get("saved") || search.get("run") || search.get("new")) return;
    try {
      const d = localStorage.getItem(draftKey(ws));
      if (d) setSql(d);
    } catch {
      /* storage unavailable */
    }
  }, [search, ws]);

  React.useEffect(() => {
    if (saved) return;
    const t = setTimeout(() => {
      try {
        localStorage.setItem(draftKey(ws), sql);
      } catch {
        /* ignore */
      }
    }, 400);
    return () => clearTimeout(t);
  }, [sql, saved, ws]);

  const names = React.useMemo(() => detectParams(sql), [sql]);
  const activeDefs = React.useMemo(() => syncParamDefs(names, defs), [names, defs]);

  const applySaved = React.useCallback((q: SavedQueryOut) => {
    setSaved(q);
    setSql(q.sql);
    setDefs(q.parameters);
    setValues(Object.fromEntries(q.parameters.map((p) => [p.name, paramValueToText(p.default)])));
    setChart((q.chart as ChartConfig | null) ?? null);
    setRun(null);
    setRunError(null);
  }, []);

  // URL-driven actions: ?saved=, ?run=, ?new=1
  const savedParam = search.get("saved");
  const runParam = search.get("run");
  const newParam = search.get("new");
  React.useEffect(() => {
    if (newParam) {
      setSaved(null);
      setSql("");
      setDefs([]);
      setValues({});
      setRun(null);
      setRunError(null);
      setChart(null);
      router.replace(href("/sql"));
      setTimeout(() => editor.current?.focus(), 0);
    }
  }, [newParam, router, href]);
  React.useEffect(() => {
    if (!savedParam) return;
    queries
      .getSaved(ws, savedParam)
      .then(applySaved)
      .catch((e: Error) => toast.error("Could not open saved query", { description: e.message }));
  }, [savedParam, ws, applySaved]);
  React.useEffect(() => {
    if (!runParam) return;
    queries
      .getRun(ws, runParam)
      .then((r) => {
        setSaved(null);
        setSql(r.sql);
        setValues(Object.fromEntries(Object.entries(r.params ?? {}).map(([k, v]) => [k, paramValueToText(v)])));
      })
      .catch((e: Error) => toast.error("Could not open query run", { description: e.message }));
  }, [runParam, ws]);

  const runMutation = useMutation({
    mutationFn: (body: Parameters<typeof queries.run>[1]) => queries.run(ws, body),
    onMutate: () => setRunError(null),
    onSuccess: (res) => {
      setRun((prev) => {
        const sameShape =
          prev && prev.result.columns.map((c) => c.name).join("|") === res.result.columns.map((c) => c.name).join("|");
        if (!sameShape && !saved?.chart) setChart(null);
        return res;
      });
    },
    onError: (e) => setRunError(e),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["ws", ws, "queries", "history"] }),
  });

  const rerunMutation = useMutation({
    mutationFn: (r: QueryRunOut) => queries.rerun(ws, r.id),
    onMutate: () => setRunError(null),
    onSuccess: (res) => {
      setSql(res.sql);
      setRun(res);
      setChart(null);
    },
    onError: (e) => setRunError(e),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["ws", ws, "queries", "history"] }),
  });

  const executeRun = React.useCallback(() => {
    const selection = editor.current?.getSelection() ?? "";
    const text = (selection.trim() ? selection : sql).trim();
    if (!text) {
      toast.message("Nothing to run", { description: "Write a SELECT query first." });
      return;
    }
    const used = detectParams(text);
    const usedDefs = activeDefs.filter((d) => used.includes(d.name));
    const errs = validateParamValues(usedDefs, values);
    setParamErrors(errs);
    if (Object.keys(errs).length) {
      toast.error("Fill in the query parameters", { description: Object.keys(errs).map((k) => `$${k}`).join(", ") });
      return;
    }
    const params: Record<string, unknown> = {};
    usedDefs.forEach((d) => {
      params[d.name] = coerceParamValue(values[d.name] ?? paramValueToText(d.default), d.type);
    });
    runMutation.mutate({ sql: normalizeParams(text), params, parameters: usedDefs, limit, suggest_chart: false });
  }, [sql, activeDefs, values, limit, runMutation]);

  const saveMutation = useMutation({
    mutationFn: async (v: SaveQueryValues) => {
      const body = {
        name: v.name,
        description: v.description,
        tags: v.tags,
        sql: normalizeParams(sql),
        parameters: activeDefs.map((d) => ({
          ...d,
          default: values[d.name] ? coerceParamValue(values[d.name], d.type) : (d.default ?? null),
        })),
        chart: chart ? (chart as unknown as Record<string, unknown>) : null,
      };
      return saved && !v.asNew ? queries.updateSaved(ws, saved.id, body) : queries.save(ws, body);
    },
    onSuccess: (q) => {
      setSaved(q);
      setSql(q.sql);
      setSaveOpen(false);
      void qc.invalidateQueries({ queryKey: qk.savedQueries(ws) });
      toast.success(`Saved “${q.name}”`, { description: `Version ${q.version_no}` });
      try {
        localStorage.removeItem(draftKey(ws));
      } catch {
        /* ignore */
      }
    },
    onError: (e: Error) => {
      if (!saveOpen) toast.error("Save failed", { description: e.message });
    },
  });

  const onSaveShortcut = React.useCallback(() => {
    if (!canEdit) {
      toast.error("Viewers cannot save queries");
      return;
    }
    if (!sql.trim()) return;
    if (saved) saveMutation.mutate({ name: saved.name, description: saved.description, tags: saved.tags, asNew: false });
    else setSaveOpen(true);
  }, [canEdit, sql, saved, saveMutation]);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        onSaveShortcut();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onSaveShortcut]);

  const schemaTables: SchemaTable[] = React.useMemo(
    () => (schemaQ.data?.tables ?? []).map((t) => ({ name: t.table, columns: t.columns })),
    [schemaQ.data],
  );
  const insert = (text: string) => {
    editor.current?.insert(text);
    setSchemaSheet(false);
  };

  const dirty = saved ? normalizeParams(sql) !== saved.sql : false;
  const noTables = schemaQ.data && schemaQ.data.tables.length === 0;

  const schemaPane = schemaQ.isLoading ? (
    <LoadingState className="px-2" />
  ) : schemaQ.isError ? (
    <ErrorState error={schemaQ.error} onRetry={() => void schemaQ.refetch()} compact />
  ) : noTables ? (
    <EmptyState
      compact
      icon={Database}
      title="No tables yet"
      description="Load the demo or upload a file to query it here."
      action={<LoadDemoButton workspaceId={ws} size="xs" label="Load demo" />}
      secondary={
        <Button asChild size="xs">
          <Link href={href("/data?upload=1")}>
            <Upload /> Upload
          </Link>
        </Button>
      }
    />
  ) : (
    <SchemaBrowser tables={schemaQ.data?.tables ?? []} onInsert={insert} />
  );

  const sidePanel = side ? (
    <QuerySidePanel
      tab={side}
      onTabChange={setSide}
      onClose={() => setSide(null)}
      currentSavedId={saved?.id}
      onOpenRun={(r) => {
        setSaved(null);
        setSql(r.sql);
        setValues(Object.fromEntries(Object.entries(r.params ?? {}).map(([k, v]) => [k, paramValueToText(v)])));
        if (!wide) setSide(null);
      }}
      onRerun={(r) => rerunMutation.mutate(r)}
      onOpenSaved={(q) => {
        applySaved(q);
        if (!wide) setSide(null);
      }}
    />
  ) : null;

  const running = runMutation.isPending || rerunMutation.isPending;

  const editorPane = (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border px-2">
        {!medium ? (
          <Button size="xs" variant="ghost" onClick={() => setSchemaSheet(true)} aria-label="Open schema browser">
            <Database /> Schema
          </Button>
        ) : null}
        <Tooltip content={`Run (${mac ? "⌘" : "Ctrl"}+Enter). Runs the selection when there is one.`}>
          <Button size="xs" variant="primary" onClick={executeRun} disabled={running}>
            <Play /> Run
          </Button>
        </Tooltip>
        <select
          aria-label="Row limit"
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
          className="h-6 rounded border border-border-strong bg-bg px-1 text-xs"
        >
          {LIMITS.map((l) => (
            <option key={l} value={l}>
              Limit {l.toLocaleString()}
            </option>
          ))}
        </select>
        <Tooltip content={`Save (${mac ? "⌘" : "Ctrl"}+S)`}>
          <Button size="xs" variant="ghost" onClick={onSaveShortcut} disabled={!sql.trim() || !canEdit || saveMutation.isPending}>
            <Save /> {saved ? "Save" : "Save…"}
          </Button>
        </Tooltip>
        <Button size="xs" variant="ghost" onClick={() => setGenOpen(true)}>
          <Wand2 /> Draft with AI
        </Button>
        <div className="ml-2 flex min-w-0 items-center gap-1.5 text-xs text-fg-subtle">
          {saved ? (
            <>
              <Bookmark className="size-3 shrink-0" />
              <span className="truncate font-medium text-fg">{saved.name}</span>
              <span className="shrink-0">v{saved.version_no}</span>
              {dirty ? <span className="shrink-0 text-warning">· edited</span> : null}
            </>
          ) : (
            <span className="truncate">Untitled query</span>
          )}
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-0.5">
          <Tooltip content="New query">
            <Button size="icon-xs" variant="ghost" aria-label="New query" onClick={() => router.push(href("/sql?new=1"))}>
              <FilePlus2 />
            </Button>
          </Tooltip>
          <Button size="xs" variant={side === "history" ? "secondary" : "ghost"} onClick={() => setSide(side === "history" ? null : "history")}>
            <History /> History
          </Button>
          <Button size="xs" variant={side === "saved" ? "secondary" : "ghost"} onClick={() => setSide(side === "saved" ? null : "saved")}>
            <Bookmark /> Saved
          </Button>
        </div>
      </div>
      <ParamBar
        defs={activeDefs}
        values={values}
        errors={paramErrors}
        onValue={(n, v) => {
          setValues((s) => ({ ...s, [n]: v }));
          setParamErrors((e) => {
            const { [n]: _drop, ...rest } = e;
            return rest;
          });
        }}
        onType={(n, t) => setDefs(activeDefs.map((d) => (d.name === n ? { ...d, type: t } : d)))}
      />
      <CodeEditor
        ref={editor}
        value={sql}
        onChange={setSql}
        language="sql"
        schema={schemaTables}
        onRun={executeRun}
        placeholder={"-- DuckDB SQL (read-only). Use $name for parameters.\n-- ⌘↵ runs the query or the current selection."}
        className="min-h-0 flex-1"
        ariaLabel="SQL editor"
        autoFocus
      />
      <div className="flex h-6 shrink-0 items-center gap-3 border-t border-border px-2 text-2xs text-fg-faint">
        <span>
          <Kbd>{mac ? "⌘" : "Ctrl"}</Kbd> <Kbd>↵</Kbd> run
        </span>
        <span>
          <Kbd>{mac ? "⌘" : "Ctrl"}</Kbd> <Kbd>S</Kbd> save
        </span>
        <span>Read-only: only SELECT / WITH / VALUES statements run.</span>
      </div>
    </div>
  );

  return (
    <div className="flex h-full min-h-0">
      <Group orientation="horizontal" className="min-w-0 flex-1">
        {medium ? (
          <>
            <Panel id="schema" defaultSize="20" minSize="12" maxSize="40" className="bg-bg-subtle">
              {schemaPane}
            </Panel>
            <ResizeHandle />
          </>
        ) : null}
        <Panel id="main" minSize="40">
          <Group orientation="vertical" className="h-full">
            <Panel id="editor" defaultSize="45" minSize="15">
              {editorPane}
            </Panel>
            <ResizeHandle orientation="vertical" />
            <Panel id="results" minSize="15">
              <ResultsPanel
                run={run}
                error={runError}
                running={running}
                chartConfig={chart}
                onChartConfigChange={setChart}
                emptyHint={
                  noTables ? "Load data first, then run a query." : (
                    <span>
                      Run a query with <Kbd>{mac ? "⌘" : "Ctrl"}</Kbd> <Kbd>↵</Kbd>. Results, charts and exports appear here.
                    </span>
                  )
                }
              />
            </Panel>
          </Group>
        </Panel>
      </Group>
      {side && wide ? <aside className={cn("w-80 shrink-0 border-l border-border bg-bg")}>{sidePanel}</aside> : null}
      <Sheet open={!!side && !wide} onOpenChange={(o) => !o && setSide(null)}>
        <SheetContent width="w-[340px]" aria-describedby={undefined}>
          <SheetTitle className="sr-only">Query history and saved queries</SheetTitle>
          {sidePanel}
        </SheetContent>
      </Sheet>
      <Sheet open={schemaSheet} onOpenChange={setSchemaSheet}>
        <SheetContent width="w-[300px]" aria-describedby={undefined} className="left-0 right-auto border-r border-l-0">
          <SheetTitle className="border-b border-border px-3 py-2 text-sm">Schema</SheetTitle>
          {schemaPane}
        </SheetContent>
      </Sheet>
      <SaveQueryDialog
        open={saveOpen}
        onOpenChange={setSaveOpen}
        initial={{ name: saved?.name ?? "", description: saved?.description ?? "", tags: saved?.tags ?? [] }}
        existing={!!saved}
        pending={saveMutation.isPending}
        error={saveMutation.error}
        onSave={(v) => saveMutation.mutate(v)}
      />
      <GenerateSqlDialog
        open={genOpen}
        onOpenChange={setGenOpen}
        onInsert={(text) => {
          setSaved(null);
          setSql(text);
          setTimeout(() => editor.current?.focus(), 0);
        }}
      />
    </div>
  );
}

export default function SqlPage() {
  return (
    <Suspense fallback={<LoadingState variant="block" label="Loading SQL workspace" />}>
      <SqlWorkspace />
    </Suspense>
  );
}
