import {
  Home,
  Database,
  Sigma,
  GitBranch,
  TerminalSquare,
  Table2,
  NotebookPen,
  Lightbulb,
  LayoutDashboard,
  FileText,
  Settings,
  Activity,
  TrendingUp,
} from "lucide-react";

export interface NavItem {
  id: string;
  label: string;
  path: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Single-key suffix for "g <key>" go-to shortcuts. */
  goKey?: string;
  keywords?: string[];
}

export const PRIMARY_NAV: NavItem[] = [
  { id: "home", label: "Home", path: "", icon: Home, goKey: "h" },
  { id: "data", label: "Data", path: "/data", icon: Database, goKey: "d", keywords: ["datasets", "sources", "upload", "tables"] },
  { id: "metrics", label: "Metrics", path: "/metrics", icon: Sigma, goKey: "m", keywords: ["semantic", "glossary", "metric trees"] },
  { id: "investigate", label: "Investigate", path: "/investigate", icon: GitBranch, goKey: "i", keywords: ["ask", "why", "question"] },
  { id: "sql", label: "SQL", path: "/sql", icon: TerminalSquare, goKey: "s", keywords: ["query", "editor"] },
  { id: "explore", label: "Explore", path: "/explore", icon: Table2, goKey: "e", keywords: ["pivot", "explorer", "no-code"] },
  {
    id: "analyze",
    label: "Analyze",
    path: "/analyze",
    icon: TrendingUp,
    goKey: "a",
    keywords: ["forecast", "anomaly", "segment", "rfm", "statistics", "t-test", "correlation", "regression"],
  },
  { id: "notebooks", label: "Notebooks", path: "/notebooks", icon: NotebookPen, goKey: "n", keywords: ["python", "cells"] },
  { id: "findings", label: "Findings", path: "/findings", icon: Lightbulb, goKey: "f", keywords: ["evidence", "review"] },
  { id: "dashboards", label: "Dashboards", path: "/dashboards", icon: LayoutDashboard, goKey: "b", keywords: ["kpi", "tiles"] },
  { id: "reports", label: "Reports", path: "/reports", icon: FileText, goKey: "r", keywords: ["business review", "memo", "export"] },
];

export const SECONDARY_NAV: NavItem[] = [
  { id: "settings", label: "Settings", path: "/settings", icon: Settings, goKey: ",", keywords: ["members", "tokens", "ai", "calendar"] },
  { id: "diagnostics", label: "Diagnostics", path: "/diagnostics", icon: Activity, keywords: ["logs", "health", "audit"] },
];

export const ALL_NAV = [...PRIMARY_NAV, ...SECONDARY_NAV];

/** Which nav item is active for a pathname under /w/{ws}. */
export function activeNavId(pathname: string): string {
  const rest = pathname.replace(/^\/w\/[^/]+/, "");
  if (!rest || rest === "/") return "home";
  const hit = ALL_NAV.filter((n) => n.path).find((n) => rest === n.path || rest.startsWith(`${n.path}/`));
  return hit?.id ?? "";
}
