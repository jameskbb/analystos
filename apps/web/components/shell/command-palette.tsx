"use client";
import * as React from "react";
import { Command } from "cmdk";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  ArrowRight,
  Database,
  FileText,
  GitBranch,
  LayoutDashboard,
  Lightbulb,
  MessageSquareText,
  Moon,
  NotebookPen,
  Plus,
  Search,
  Sigma,
  Table2,
  TerminalSquare,
  BookOpen,
  Columns3,
  PackageOpen,
  Keyboard,
} from "lucide-react";
import type { SearchHit } from "@/lib/api/types";
import { ALL_NAV } from "./nav";
import { Kbd } from "@/components/ui/kbd";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

export interface PaletteAction {
  id: string;
  label: string;
  group: "Navigate" | "Create" | "Actions";
  icon: React.ComponentType<{ className?: string }>;
  keywords?: string[];
  shortcut?: string[];
  run: () => void;
}

const HIT_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  dataset: Database,
  table: Database,
  column: Columns3,
  metric: Sigma,
  dimension: Columns3,
  glossary: BookOpen,
  investigation: GitBranch,
  finding: Lightbulb,
  dashboard: LayoutDashboard,
  report: FileText,
  saved_query: TerminalSquare,
  notebook: NotebookPen,
};

/** Maps a search hit to an in-app path (relative to the workspace root). */
export function hitPath(hit: SearchHit): string {
  const id = hit.ref_id ?? hit.id;
  switch (hit.kind) {
    case "dataset":
    case "table":
      return `/data/${id}`;
    case "column":
      return hit.parent_id ? `/data/${hit.parent_id}?tab=schema&column=${encodeURIComponent(hit.title)}` : "/data";
    case "metric":
      return `/metrics/${id}`;
    case "dimension":
      return `/metrics?tab=dimensions&q=${encodeURIComponent(hit.title)}`;
    case "glossary":
      return `/metrics/glossary?q=${encodeURIComponent(hit.title)}`;
    case "investigation":
      return `/investigate/${id}`;
    case "finding":
      return `/findings/${id}`;
    case "dashboard":
      return `/dashboards/${id}`;
    case "report":
      return `/reports/${id}`;
    case "saved_query":
      return `/sql?saved=${id}`;
    case "notebook":
      return `/notebooks/${id}`;
    default:
      return "";
  }
}

/** Simple deterministic scorer for static commands (prefix > word-start > substring). */
export function scoreCommand(query: string, text: string, keywords: string[] = []): number {
  const q = query.trim().toLowerCase();
  if (!q) return 1;
  const hay = [text, ...keywords].map((s) => s.toLowerCase());
  let best = 0;
  for (const h of hay) {
    if (h.startsWith(q)) best = Math.max(best, 3);
    else if (h.split(/[\s/_-]+/).some((w) => w.startsWith(q))) best = Math.max(best, 2);
    else if (h.includes(q)) best = Math.max(best, 1);
  }
  return best;
}

export function buildDefaultActions(opts: {
  navigate: (path: string) => void;
  toggleTheme: () => void;
  openShortcuts: () => void;
  loadDemo?: () => void;
}): PaletteAction[] {
  const nav: PaletteAction[] = ALL_NAV.map((n) => ({
    id: `nav:${n.id}`,
    label: `Go to ${n.label}`,
    group: "Navigate",
    icon: n.icon,
    keywords: [n.label, ...(n.keywords ?? [])],
    shortcut: n.goKey ? ["G", n.goKey.toUpperCase()] : undefined,
    run: () => opts.navigate(n.path),
  }));
  const create: PaletteAction[] = [
    { id: "new:investigation", label: "New investigation", group: "Create", icon: GitBranch, keywords: ["ask", "question", "why"], shortcut: ["N"], run: () => opts.navigate("/investigate?new=1") },
    { id: "new:query", label: "New SQL query", group: "Create", icon: TerminalSquare, keywords: ["sql", "editor"], run: () => opts.navigate("/sql?new=1") },
    { id: "new:explore", label: "Explore a table", group: "Create", icon: Table2, keywords: ["pivot", "no code"], run: () => opts.navigate("/explore") },
    { id: "new:notebook", label: "New notebook", group: "Create", icon: NotebookPen, run: () => opts.navigate("/notebooks?new=1") },
    { id: "new:dashboard", label: "New dashboard", group: "Create", icon: LayoutDashboard, run: () => opts.navigate("/dashboards?new=1") },
    { id: "new:report", label: "New report", group: "Create", icon: FileText, run: () => opts.navigate("/reports?new=1") },
    { id: "new:review", label: "Start a business review", group: "Create", icon: FileText, keywords: ["monthly", "mbr"], run: () => opts.navigate("/reports/review") },
    { id: "new:upload", label: "Upload a file", group: "Create", icon: Database, keywords: ["csv", "excel", "parquet", "json", "import"], run: () => opts.navigate("/data?upload=1") },
    { id: "new:metric", label: "Define a metric", group: "Create", icon: Sigma, run: () => opts.navigate("/metrics?new=1") },
  ];
  const actions: PaletteAction[] = [
    { id: "act:theme", label: "Toggle light / dark theme", group: "Actions", icon: Moon, keywords: ["dark mode", "appearance"], run: opts.toggleTheme },
    { id: "act:shortcuts", label: "Keyboard shortcuts", group: "Actions", icon: Keyboard, shortcut: ["?"], run: opts.openShortcuts },
    { id: "act:quality", label: "Open Data Quality Center", group: "Actions", icon: Database, keywords: ["dq", "rules"], run: () => opts.navigate("/data/quality") },
    { id: "act:relationships", label: "Review relationships", group: "Actions", icon: Database, keywords: ["joins", "keys"], run: () => opts.navigate("/data/relationships") },
    { id: "act:glossary", label: "Open business glossary", group: "Actions", icon: BookOpen, run: () => opts.navigate("/metrics/glossary") },
  ];
  if (opts.loadDemo)
    actions.push({ id: "act:demo", label: "Load Summit Supply demo", group: "Actions", icon: PackageOpen, keywords: ["sample", "demo data"], run: opts.loadDemo });
  return [...create, ...nav, ...actions];
}

export function CommandPalette({
  open,
  onOpenChange,
  actions,
  search,
  onNavigate,
  onAsk,
  initialQuery = "",
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  actions: PaletteAction[];
  /** Workspace search. Omit to disable remote search. */
  search?: (q: string, signal: AbortSignal) => Promise<SearchHit[]>;
  onNavigate: (path: string) => void;
  /** Launches an investigation for a free-text question. */
  onAsk?: (question: string) => void;
  initialQuery?: string;
}) {
  const [query, setQuery] = React.useState(initialQuery);
  const [hits, setHits] = React.useState<SearchHit[]>([]);
  const [searching, setSearching] = React.useState(false);
  const [searchError, setSearchError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (open) setQuery(initialQuery);
  }, [open, initialQuery]);

  React.useEffect(() => {
    if (!search || query.trim().length < 2) {
      setHits([]);
      setSearching(false);
      setSearchError(null);
      return;
    }
    const ctrl = new AbortController();
    setSearching(true);
    const t = setTimeout(() => {
      search(query.trim(), ctrl.signal)
        .then((h) => {
          setHits(h);
          setSearchError(null);
        })
        .catch((e: unknown) => {
          if (!(e instanceof DOMException && e.name === "AbortError")) {
            setHits([]);
            setSearchError(e instanceof Error ? e.message : "Search failed");
          }
        })
        .finally(() => setSearching(false));
    }, 150);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [query, search]);

  const close = () => onOpenChange(false);
  const runAndClose = (fn: () => void) => {
    close();
    fn();
  };

  const filtered = actions
    .map((a) => ({ a, s: scoreCommand(query, a.label, a.keywords) }))
    .filter((x) => x.s > 0)
    .sort((x, y) => y.s - x.s);
  const groups: PaletteAction["group"][] = query.trim() ? ["Create", "Navigate", "Actions"] : ["Create", "Navigate", "Actions"];
  const looksLikeQuestion = query.trim().length > 3;
  // Ask ranks first unless the text is clearly a command (a strong prefix/word match).
  const askFirst = !filtered.some((x) => x.s >= 2);
  const askGroup =
    onAsk && looksLikeQuestion ? (
      <Command.Group heading="Ask" className={groupCls}>
                  <Command.Item
                    value={`ask:${query}`}
                    onSelect={() => runAndClose(() => onAsk(query.trim()))}
                    className={itemCls}
                  >
                    <MessageSquareText className="size-3.5 text-accent" />
                    <span className="truncate">
                      Investigate: <span className="font-medium">&ldquo;{query.trim()}&rdquo;</span>
                    </span>
                    <ArrowRight className="ml-auto size-3.5 text-fg-faint" />
                  </Command.Item>
                </Command.Group>
    ) : null;

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/30" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          className="fixed top-[12vh] left-1/2 z-50 w-[calc(100vw-24px)] max-w-xl -translate-x-1/2 overflow-hidden rounded-lg border border-border-strong bg-bg shadow-pop focus:outline-none"
        >
          <DialogPrimitive.Title className="sr-only">Command palette</DialogPrimitive.Title>
          <Command shouldFilter={false} loop label="Command palette" className="flex flex-col">
            <div className="flex items-center gap-2 border-b border-border px-3">
              <Search className="size-4 shrink-0 text-fg-subtle" aria-hidden />
              <Command.Input
                value={query}
                onValueChange={setQuery}
                autoFocus
                placeholder="Search data, metrics, findings… or ask a question"
                className="h-11 w-full bg-transparent text-base outline-none placeholder:text-fg-faint"
                aria-label="Search or type a command"
              />
              {searching ? <Spinner /> : null}
            </div>
            <Command.List className="max-h-[60vh] overflow-y-auto p-1.5 scrollbar-thin">
              <Command.Empty className="px-3 py-6 text-center text-sm text-fg-subtle">
                {searching ? "Searching…" : searchError ? `Search unavailable: ${searchError}` : "No matches."}
              </Command.Empty>

              {askFirst ? askGroup : null}

              {hits.length ? (
                <Command.Group heading="Workspace" className={groupCls}>
                  {hits.map((h) => {
                    const Icon = HIT_ICON[h.kind] ?? Search;
                    const path = hitPath(h);
                    return (
                      <Command.Item
                        key={`${h.kind}:${h.id}`}
                        value={`hit:${h.kind}:${h.id}`}
                        disabled={!path}
                        onSelect={() => runAndClose(() => onNavigate(path))}
                        className={itemCls}
                      >
                        <Icon className="size-3.5 text-fg-subtle" />
                        <span className="min-w-0 truncate">{h.title}</span>
                        {h.subtitle ? <span className="min-w-0 truncate text-xs text-fg-subtle">{h.subtitle}</span> : null}
                        <span className="ml-auto shrink-0 text-2xs tracking-wide text-fg-faint uppercase">
                          {h.kind.replace("_", " ")}
                        </span>
                      </Command.Item>
                    );
                  })}
                </Command.Group>
              ) : null}

              {groups.map((g) => {
                const items = filtered.filter((x) => x.a.group === g).slice(0, query ? 6 : 20);
                if (!items.length) return null;
                return (
                  <Command.Group key={g} heading={g} className={groupCls}>
                    {items.map(({ a }) => (
                      <Command.Item key={a.id} value={a.id} onSelect={() => runAndClose(a.run)} className={itemCls}>
                        <a.icon className="size-3.5 text-fg-subtle" />
                        <span className="truncate">{a.label}</span>
                        {a.shortcut ? (
                          <span className="ml-auto flex gap-0.5">
                            {a.shortcut.map((k) => (
                              <Kbd key={k}>{k}</Kbd>
                            ))}
                          </span>
                        ) : null}
                      </Command.Item>
                    ))}
                  </Command.Group>
                );
              })}
              {!askFirst ? askGroup : null}
            </Command.List>
            <div className="flex items-center gap-3 border-t border-border px-3 py-1.5 text-2xs text-fg-subtle">
              <span className="flex items-center gap-1">
                <Kbd>↑</Kbd>
                <Kbd>↓</Kbd> navigate
              </span>
              <span className="flex items-center gap-1">
                <Kbd>↵</Kbd> open
              </span>
              <span className="flex items-center gap-1">
                <Kbd>esc</Kbd> close
              </span>
              <span className="ml-auto flex items-center gap-1">
                <Plus className="size-3" /> type a question to start an investigation
              </span>
            </div>
          </Command>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

const groupCls = cn(
  "[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:text-2xs [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-fg-subtle [&_[cmdk-group-heading]]:uppercase",
);
const itemCls =
  "flex h-8 cursor-default items-center gap-2 rounded px-2 text-sm select-none data-[disabled=true]:opacity-50 data-[selected=true]:bg-bg-muted";
