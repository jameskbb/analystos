"use client";
import * as React from "react";
import { AlertTriangle, RefreshCw, Inbox, Lock, SearchX } from "lucide-react";
import type { UseQueryResult } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";

export function LoadingState({
  label = "Loading",
  rows = 4,
  className,
  variant = "rows",
}: {
  label?: string;
  rows?: number;
  className?: string;
  variant?: "rows" | "inline" | "block";
}) {
  if (variant === "inline")
    return (
      <div className={cn("flex items-center gap-2 text-sm text-fg-subtle", className)} role="status" aria-live="polite">
        <Spinner label={label} /> {label}…
      </div>
    );
  if (variant === "block")
    return (
      <div className={cn("flex h-full min-h-32 items-center justify-center", className)} role="status" aria-live="polite">
        <div className="flex items-center gap-2 text-sm text-fg-subtle">
          <Spinner label={label} /> {label}…
        </div>
      </div>
    );
  return (
    <div className={cn("flex flex-col gap-2 py-2", className)} role="status" aria-live="polite" aria-label={label}>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-7" style={{ width: `${90 - ((i * 13) % 35)}%` }} />
      ))}
      <span className="sr-only">{label}…</span>
    </div>
  );
}

export function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  action,
  secondary,
  className,
  compact = false,
}: {
  icon?: React.ComponentType<{ className?: string }>;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  secondary?: React.ReactNode;
  className?: string;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center",
        compact ? "gap-1.5 px-4 py-6" : "gap-2 px-6 py-14",
        className,
      )}
    >
      <div className="flex size-8 items-center justify-center rounded-md border border-border bg-bg-subtle">
        <Icon className="size-4 text-fg-subtle" />
      </div>
      <div className="text-sm font-medium text-fg">{title}</div>
      {description ? <div className="max-w-md text-sm text-fg-subtle">{description}</div> : null}
      {action || secondary ? (
        <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
          {action}
          {secondary}
        </div>
      ) : null}
    </div>
  );
}

export function ErrorState({
  error,
  title,
  onRetry,
  className,
  compact = false,
}: {
  error: unknown;
  title?: string;
  onRetry?: () => void;
  className?: string;
  compact?: boolean;
}) {
  const err = error instanceof ApiError ? error : null;
  const message = error instanceof Error ? error.message : String(error ?? "Unknown error");
  const Icon = err?.isForbidden ? Lock : err?.isNotFound ? SearchX : AlertTriangle;
  const heading =
    title ?? (err?.isForbidden ? "You don't have access" : err?.isNotFound ? "Not found" : "Something went wrong");
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center gap-2 text-center",
        compact ? "px-4 py-6" : "px-6 py-14",
        className,
      )}
    >
      <div className="flex size-8 items-center justify-center rounded-md border border-negative/30 bg-negative-soft">
        <Icon className="size-4 text-negative" />
      </div>
      <div className="text-sm font-medium">{heading}</div>
      <div className="max-w-lg text-sm break-words text-fg-subtle">{message}</div>
      {onRetry && !err?.isForbidden && !err?.isNotFound ? (
        <Button size="xs" variant="secondary" onClick={onRetry} className="mt-1">
          <RefreshCw /> Retry
        </Button>
      ) : null}
    </div>
  );
}

/** Renders loading / error / empty / data states for a TanStack query. */
export function QueryState<T>({
  query,
  children,
  empty,
  isEmpty,
  loading,
  errorTitle,
  compact,
}: {
  query: Pick<UseQueryResult<T>, "data" | "isLoading" | "error" | "refetch" | "isError">;
  children: (data: T) => React.ReactNode;
  empty?: React.ReactNode;
  isEmpty?: (data: T) => boolean;
  loading?: React.ReactNode;
  errorTitle?: string;
  compact?: boolean;
}) {
  if (query.isLoading) return <>{loading ?? <LoadingState />}</>;
  if (query.isError)
    return <ErrorState error={query.error} title={errorTitle} onRetry={() => void query.refetch()} compact={compact} />;
  if (query.data === undefined) return <>{loading ?? <LoadingState />}</>;
  const data = query.data;
  const emptyCheck = isEmpty ?? ((d: T) => Array.isArray(d) && d.length === 0);
  if (empty && emptyCheck(data)) return <>{empty}</>;
  return <>{children(data)}</>;
}

export function InlineError({ error, className }: { error: unknown; className?: string }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-2 rounded border border-negative/30 bg-negative-soft px-2.5 py-1.5 text-sm text-negative",
        className,
      )}
    >
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
      <span className="min-w-0 break-words whitespace-pre-wrap">{message}</span>
    </div>
  );
}
