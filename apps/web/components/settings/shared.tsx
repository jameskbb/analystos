"use client";
import * as React from "react";
import { Lock } from "lucide-react";
import { cn } from "@/lib/utils";

export function SettingsSection({
  title,
  description,
  children,
  actions,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("grid gap-4 border-b border-border py-5 first:pt-0 last:border-b-0 lg:grid-cols-[260px_1fr]", className)}>
      <div>
        <h2 className="text-sm font-semibold">{title}</h2>
        {description ? <p className="mt-1 text-xs text-fg-subtle">{description}</p> : null}
        {actions ? <div className="mt-2">{actions}</div> : null}
      </div>
      <div className="min-w-0">{children}</div>
    </section>
  );
}

export function RoleNotice({ required = "owner" }: { required?: "owner" | "editor" }) {
  return (
    <div className="mb-3 flex items-center gap-2 rounded border border-border bg-bg-subtle px-2.5 py-1.5 text-xs text-fg-muted">
      <Lock className="size-3.5 shrink-0 text-fg-subtle" aria-hidden />
      Read-only: only workspace {required}s can change these settings.
    </div>
  );
}
