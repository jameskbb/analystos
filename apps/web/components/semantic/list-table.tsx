import * as React from "react";
import { cn } from "@/lib/utils";

/** Dense list table used across the semantic screens (sticky header, 1px row rules). */
export function ListTable({ children, className, label }: { children: React.ReactNode; className?: string; label: string }) {
  return (
    <div className={cn("overflow-auto rounded-md border border-border scrollbar-thin", className)}>
      <table className="w-full min-w-[720px] border-collapse text-sm" aria-label={label}>
        {children}
      </table>
    </div>
  );
}

export function Th({ children, className }: { children?: React.ReactNode; className?: string }) {
  return (
    <th
      scope="col"
      className={cn(
        "sticky top-0 z-[1] h-8 border-b border-border bg-bg-subtle px-2.5 text-left text-xs font-medium whitespace-nowrap text-fg-subtle",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({ children, className, colSpan }: { children?: React.ReactNode; className?: string; colSpan?: number }) {
  return (
    <td colSpan={colSpan} className={cn("h-9 border-b border-border/70 px-2.5 align-middle", className)}>
      {children}
    </td>
  );
}

export function Tr({ children, className }: { children: React.ReactNode; className?: string }) {
  return <tr className={cn("hover:bg-bg-subtle", className)}>{children}</tr>;
}
