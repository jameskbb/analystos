"use client";
import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

/** Safe markdown (no raw HTML) with the app's prose styles. */
export function Markdown({ children, className }: { children: string | null | undefined; className?: string }) {
  if (!children?.trim()) return null;
  return (
    <div className={cn("prose-aos", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
