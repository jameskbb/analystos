"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BookOpen, GitFork, Sigma } from "lucide-react";
import { useWorkspace } from "@/components/providers/workspace";
import { cn } from "@/lib/utils";

const ITEMS = [
  { path: "/metrics", label: "Metrics & model", icon: Sigma, exact: true },
  { path: "/metrics/trees", label: "Driver trees", icon: GitFork },
  { path: "/metrics/glossary", label: "Glossary", icon: BookOpen },
];

/** Secondary navigation for the semantic layer area. */
export function SemanticNav() {
  const { href } = useWorkspace();
  const pathname = usePathname().replace(/^\/w\/[^/]+/, "");
  return (
    <nav aria-label="Semantic layer" className="inline-flex h-7 items-center rounded border border-border-strong bg-bg-subtle p-0.5">
      {ITEMS.map((it) => {
        const active = it.exact ? pathname === it.path : pathname.startsWith(it.path);
        return (
          <Link
            key={it.path}
            href={href(it.path)}
            aria-current={active ? "page" : undefined}
            className={cn(
              "inline-flex h-full items-center gap-1.5 rounded-sm px-2 text-xs text-fg-subtle hover:text-fg",
              active && "bg-bg font-medium text-fg shadow-[0_0_0_1px_var(--border)]",
            )}
          >
            <it.icon className="size-3.5" />
            {it.label}
          </Link>
        );
      })}
    </nav>
  );
}
