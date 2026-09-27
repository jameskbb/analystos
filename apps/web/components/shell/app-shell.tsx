"use client";
import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Check,
  ChevronsUpDown,
  LogOut,
  Menu,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Sun,
  Keyboard,
  MessageSquareText,
} from "lucide-react";
import { auth, investigations, search as searchApi, workspaces } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { useSession } from "@/components/providers/session";
import { useTheme } from "@/components/providers/theme";
import { useWorkspace } from "@/components/providers/workspace";
import { PRIMARY_NAV, SECONDARY_NAV, activeNavId, type NavItem } from "./nav";
import { CommandPalette, buildDefaultActions } from "./command-palette";
import { ShortcutsDialog } from "./shortcuts-dialog";
import { useLoadDemo } from "./use-load-demo";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Tooltip } from "@/components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { InlineError } from "@/components/states/states";
import { cn, isMac, isTypingTarget } from "@/lib/utils";

const COLLAPSE_KEY = "aos-sidebar-collapsed";

/** Brand mark: a small bracketed tree glyph, drawn not iconified. */
function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={cn("size-4", className)} aria-hidden>
      <rect x="0.5" y="0.5" width="15" height="15" rx="3" fill="var(--fg)" />
      <path d="M4 4.5h3M5.5 4.5v7M5.5 8h4M5.5 11.5h6" stroke="var(--bg)" strokeWidth="1.4" strokeLinecap="round" fill="none" />
    </svg>
  );
}

function NavLink({ item, active, collapsed, onNavigate }: { item: NavItem; active: boolean; collapsed: boolean; onNavigate?: () => void }) {
  const { href } = useWorkspace();
  const link = (
    <Link
      href={href(item.path)}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex h-7 items-center gap-2.5 rounded px-2 text-sm text-fg-muted hover:bg-bg-muted hover:text-fg",
        active && "bg-bg-muted font-medium text-fg",
        collapsed && "justify-center px-0",
      )}
    >
      <item.icon className={cn("size-4 shrink-0", active ? "text-fg" : "text-fg-subtle")} />
      {collapsed ? <span className="sr-only">{item.label}</span> : <span className="truncate">{item.label}</span>}
    </Link>
  );
  return collapsed ? (
    <Tooltip content={item.label} side="right">
      {link}
    </Tooltip>
  ) : (
    link
  );
}

function WorkspaceSwitcher({ collapsed }: { collapsed: boolean }) {
  const { id, workspace } = useWorkspace();
  const router = useRouter();
  const qc = useQueryClient();
  const { data: list } = useQuery({ queryKey: qk.workspaces, queryFn: workspaces.list });
  const [creating, setCreating] = React.useState(false);
  const [name, setName] = React.useState("");
  const create = useMutation({
    mutationFn: () => workspaces.create({ name: name.trim() }),
    onSuccess: (ws) => {
      void qc.invalidateQueries({ queryKey: qk.workspaces });
      setCreating(false);
      setName("");
      router.push(`/w/${ws.id}`);
    },
  });
  const label = workspace?.name ?? "Workspace";
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={cn(
              "flex h-8 w-full min-w-0 items-center gap-2 rounded px-1.5 text-left hover:bg-bg-muted",
              collapsed && "justify-center px-0",
            )}
            aria-label={`Workspace: ${label}. Switch workspace`}
          >
            <span className="flex size-5 shrink-0 items-center justify-center rounded-sm border border-border-strong bg-bg-subtle text-2xs font-semibold">
              {label.slice(0, 1).toUpperCase()}
            </span>
            {collapsed ? null : (
              <>
                <span className="min-w-0 flex-1 truncate text-sm font-medium">{label}</span>
                <ChevronsUpDown className="size-3.5 shrink-0 text-fg-subtle" />
              </>
            )}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-60">
          <DropdownMenuLabel>Workspaces</DropdownMenuLabel>
          {(list ?? []).map((w) => (
            <DropdownMenuItem key={w.id} onSelect={() => router.push(`/w/${w.id}`)}>
              <span className="min-w-0 flex-1 truncate">{w.name}</span>
              {w.id === id ? <Check className="size-3.5" /> : null}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setCreating(true)}>
            <Plus /> New workspace
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent size="sm">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) create.mutate();
            }}
          >
            <DialogHeader>
              <DialogTitle>New workspace</DialogTitle>
            </DialogHeader>
            <DialogBody className="flex flex-col gap-3">
              <Field label="Name" htmlFor="ws-name">
                <Input id="ws-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Retail Operations" />
              </Field>
              <InlineError error={create.error} />
            </DialogBody>
            <DialogFooter>
              <Button variant="secondary" onClick={() => setCreating(false)}>
                Cancel
              </Button>
              <Button variant="primary" type="submit" disabled={!name.trim() || create.isPending}>
                Create
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}

function UserMenu({ onShortcuts }: { onShortcuts: () => void }) {
  const { data: session } = useSession();
  const { pref, setPref } = useTheme();
  const router = useRouter();
  const qc = useQueryClient();
  const logout = useMutation({
    mutationFn: auth.logout,
    onSuccess: () => {
      qc.clear();
      router.replace("/login");
    },
  });
  const user = session?.user;
  const initials = (user?.name ?? "?")
    .split(/\s+/)
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex size-7 items-center justify-center rounded-full border border-border-strong bg-bg-subtle text-2xs font-semibold hover:bg-bg-muted"
          aria-label="Account menu"
        >
          {initials}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <div className="px-2 py-1.5">
          <div className="truncate text-sm font-medium">{user?.name ?? "Unknown user"}</div>
          <div className="truncate text-xs text-fg-subtle">
            {session?.auth_mode === "local" ? "Local mode (single user)" : user?.email}
          </div>
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        {(
          [
            ["light", "Light", Sun],
            ["dark", "Dark", Moon],
            ["system", "System", Monitor],
          ] as const
        ).map(([v, l, Icon]) => (
          <DropdownMenuItem key={v} onSelect={() => setPref(v)}>
            <Icon /> {l}
            {pref === v ? <Check className="ml-auto size-3.5" /> : null}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={onShortcuts}>
          <Keyboard /> Keyboard shortcuts
          <Kbd className="ml-auto">?</Kbd>
        </DropdownMenuItem>
        {session?.auth_mode === "password" ? (
          <DropdownMenuItem onSelect={() => logout.mutate()}>
            <LogOut /> Sign out
          </DropdownMenuItem>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { id: wsId, href } = useWorkspace();
  const { toggle } = useTheme();
  const [collapsed, setCollapsed] = React.useState(false);
  const [mobileOpen, setMobileOpen] = React.useState(false);
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [paletteQuery, setPaletteQuery] = React.useState("");
  const [shortcutsOpen, setShortcutsOpen] = React.useState(false);
  const loadDemo = useLoadDemo(wsId);
  const active = activeNavId(pathname);
  const [mac, setMac] = React.useState(true);

  React.useEffect(() => {
    setMac(isMac());
    try {
      setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1");
    } catch {
      /* ignore */
    }
  }, []);
  React.useEffect(() => setMobileOpen(false), [pathname]);

  const toggleCollapsed = () => {
    setCollapsed((c) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1");
      } catch {
        /* ignore */
      }
      return !c;
    });
  };

  const navigate = React.useCallback((path: string) => router.push(href(path)), [router, href]);

  const ask = useMutation({
    mutationFn: (question: string) => investigations.create(wsId, { question }),
    onSuccess: (inv) => router.push(href(`/investigate/${inv.id}`)),
    onError: (e: Error) => toast.error("Could not start the investigation", { description: e.message }),
  });

  const openPalette = (q = "") => {
    setPaletteQuery(q);
    setPaletteOpen(true);
  };

  // Global keyboard shortcuts.
  React.useEffect(() => {
    let pendingG = 0;
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        openPalette();
        return;
      }
      if (mod && e.key === "/") {
        e.preventDefault();
        openPalette();
        return;
      }
      if (mod || e.altKey || isTypingTarget(e.target)) return;
      if (document.querySelector("[role=dialog]")) return;
      if (e.key === "?") {
        e.preventDefault();
        setShortcutsOpen(true);
        return;
      }
      if (pendingG && Date.now() - pendingG < 1200) {
        pendingG = 0;
        const item = [...PRIMARY_NAV, ...SECONDARY_NAV].find((n) => n.goKey === e.key.toLowerCase());
        if (item) {
          e.preventDefault();
          navigate(item.path);
        }
        return;
      }
      if (e.key === "g") {
        pendingG = Date.now();
        return;
      }
      if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        navigate("/investigate?new=1");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  const actions = React.useMemo(
    () =>
      buildDefaultActions({
        navigate,
        toggleTheme: toggle,
        openShortcuts: () => setShortcutsOpen(true),
        loadDemo: () => loadDemo.mutate(),
      }),
    // loadDemo.mutate is stable
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [navigate, toggle],
  );

  const searchFn = React.useCallback(
    (q: string, signal: AbortSignal) => searchApi.query(wsId, q, { limit: 20 }, signal),
    [wsId],
  );

  const sidebar = (isMobile: boolean) => {
    const c = collapsed && !isMobile;
    return (
      <nav aria-label="Primary" className="flex h-full flex-col gap-1 px-2 py-2">
        <div className={cn("mb-1 flex items-center gap-2 px-1.5", c && "justify-center px-0")}>
          <Mark />
          {c ? null : <span className="text-sm font-semibold tracking-tight">AnalystOS</span>}
        </div>
        <WorkspaceSwitcher collapsed={c} />
        <div className="my-1 h-px bg-border" />
        <div className="flex flex-col gap-px">
          {PRIMARY_NAV.map((item) => (
            <NavLink key={item.id} item={item} active={active === item.id} collapsed={c} onNavigate={() => setMobileOpen(false)} />
          ))}
        </div>
        <div className="mt-auto flex flex-col gap-px">
          {SECONDARY_NAV.map((item) => (
            <NavLink key={item.id} item={item} active={active === item.id} collapsed={c} />
          ))}
          {!isMobile ? (
            <button
              type="button"
              onClick={toggleCollapsed}
              className={cn(
                "mt-1 flex h-7 items-center gap-2.5 rounded px-2 text-sm text-fg-subtle hover:bg-bg-muted hover:text-fg",
                c && "justify-center px-0",
              )}
              aria-label={c ? "Expand sidebar" : "Collapse sidebar"}
            >
              {c ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
              {c ? null : <span>Collapse</span>}
            </button>
          ) : null}
        </div>
      </nav>
    );
  };

  return (
    <div className="flex h-dvh overflow-hidden bg-bg">
      <a
        href="#main"
        className="sr-only z-[100] rounded bg-accent px-2 py-1 text-accent-fg focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      <aside
        data-app-chrome
        className={cn(
          "hidden shrink-0 border-r border-border bg-bg-subtle transition-[width] duration-150 md:block",
          collapsed ? "w-12" : "w-52",
        )}
      >
        {sidebar(false)}
      </aside>
      {mobileOpen ? (
        <div className="fixed inset-0 z-40 md:hidden" data-app-chrome>
          <div className="absolute inset-0 bg-black/30" onClick={() => setMobileOpen(false)} aria-hidden />
          <aside className="absolute inset-y-0 left-0 w-60 border-r border-border bg-bg-subtle shadow-pop">{sidebar(true)}</aside>
        </div>
      ) : null}
      <div className="flex min-w-0 flex-1 flex-col">
        <header data-app-chrome className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-2 sm:px-3">
          <Button variant="ghost" size="icon" className="md:hidden" onClick={() => setMobileOpen(true)} aria-label="Open navigation">
            <Menu />
          </Button>
          <button
            type="button"
            onClick={() => openPalette()}
            className="flex h-7 w-full max-w-md min-w-0 items-center gap-2 rounded border border-border bg-bg-subtle px-2 text-sm text-fg-faint hover:border-border-strong"
            aria-label="Search or run a command"
          >
            <Search className="size-3.5 shrink-0" />
            <span className="truncate">Search or jump to…</span>
            <span className="ml-auto hidden gap-0.5 sm:flex">
              <Kbd>{mac ? "⌘" : "Ctrl"}</Kbd>
              <Kbd>K</Kbd>
            </span>
          </button>
          <Button variant="secondary" size="sm" onClick={() => navigate("/investigate?new=1")} className="hidden sm:inline-flex">
            <MessageSquareText /> Ask
          </Button>
          <div className="ml-auto flex items-center gap-1">
            <Tooltip content="Toggle theme">
              <Button variant="ghost" size="icon" onClick={toggle} aria-label="Toggle theme">
                <Sun className="hidden dark:block" />
                <Moon className="dark:hidden" />
              </Button>
            </Tooltip>
            <UserMenu onShortcuts={() => setShortcutsOpen(true)} />
          </div>
        </header>
        <main id="main" data-print-root className="min-h-0 flex-1 overflow-hidden">
          {children}
        </main>
      </div>
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        actions={actions}
        search={searchFn}
        onNavigate={navigate}
        onAsk={(q) => ask.mutate(q)}
        initialQuery={paletteQuery}
      />
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </div>
  );
}
