import * as React from "react";
import { cn } from "@/lib/utils";

/** Standard page frame: sticky header with title/actions, scrollable body. */
export function Page({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("flex h-full min-h-0 flex-col", className)}>{children}</div>;
}

export function PageHeader({
  title,
  description,
  actions,
  breadcrumb,
  meta,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  breadcrumb?: React.ReactNode;
  meta?: React.ReactNode;
  className?: string;
}) {
  return (
    <header className={cn("shrink-0 border-b border-border bg-bg px-4 py-3 sm:px-5", className)}>
      {breadcrumb ? <div className="mb-1 flex items-center gap-1 text-xs text-fg-subtle">{breadcrumb}</div> : null}
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold tracking-tight">{title}</h1>
          {description ? <p className="mt-0.5 text-sm text-fg-subtle">{description}</p> : null}
          {meta ? <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-fg-subtle">{meta}</div> : null}
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap items-center gap-1.5">{actions}</div> : null}
      </div>
    </header>
  );
}

export function PageBody({
  children,
  className,
  padded = true,
}: {
  children: React.ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <div className={cn("min-h-0 flex-1 overflow-auto scrollbar-thin", padded && "px-4 py-4 sm:px-5", className)}>
      {children}
    </div>
  );
}

/** Bordered panel with an optional title row. The basic unit of dense layouts. */
export function Panel({
  title,
  actions,
  children,
  className,
  bodyClassName,
  description,
  id,
}: {
  title?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
  description?: React.ReactNode;
  id?: string;
}) {
  return (
    <section id={id} className={cn("flex min-w-0 flex-col rounded-md border border-border bg-bg", className)}>
      {title || actions ? (
        <div className="flex min-h-9 items-center justify-between gap-2 border-b border-border px-3 py-1.5">
          <div className="min-w-0">
            {title ? <h2 className="truncate text-sm font-semibold">{title}</h2> : null}
            {description ? <p className="truncate text-xs text-fg-subtle">{description}</p> : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-1">{actions}</div> : null}
        </div>
      ) : null}
      <div className={cn("min-h-0 flex-1", bodyClassName)}>{children}</div>
    </section>
  );
}

/** Label/value rows for metadata sidebars. */
export function DefinitionList({
  items,
  className,
}: {
  items: { label: React.ReactNode; value: React.ReactNode; mono?: boolean }[];
  className?: string;
}) {
  return (
    <dl className={cn("grid grid-cols-[minmax(90px,max-content)_1fr] gap-x-4 gap-y-1.5 text-sm", className)}>
      {items.map((it, i) => (
        <React.Fragment key={i}>
          <dt className="text-fg-subtle">{it.label}</dt>
          <dd className={cn("min-w-0 break-words", it.mono && "font-mono text-xs leading-5")}>{it.value}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

export function SectionLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("text-2xs font-semibold tracking-wider text-fg-subtle uppercase", className)}>{children}</div>
  );
}
