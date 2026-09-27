"use client";
import * as React from "react";
import { toast } from "sonner";
import { Download, Printer } from "lucide-react";
import type { ExportFormat } from "@/lib/api/types";
import { ApiError, downloadFromApi, request } from "@/lib/api/client";
import { exportsApi, type ExportTarget } from "@/lib/api/endpoints";
import { useWorkspace } from "@/components/providers/workspace";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const FORMAT_LABEL: Record<ExportFormat, string> = {
  csv: "CSV",
  xlsx: "Excel (.xlsx)",
  png: "PNG image",
  pdf: "PDF",
  md: "Markdown",
  html: "HTML",
  ipynb: "Jupyter notebook (.ipynb)",
  json: "JSON",
};

/**
 * Prints an HTML document through a hidden iframe (the browser's "Save as PDF"). Used when the
 * server has no PDF engine: its HTML export carries print CSS, so the printed PDF matches.
 */
export function printHtml(html: string, doc: Document = document): HTMLIFrameElement {
  const frame = doc.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden";
  frame.srcdoc = html;
  frame.onload = () => {
    const w = frame.contentWindow;
    if (!w) return;
    w.addEventListener("afterprint", () => setTimeout(() => frame.remove(), 500));
    w.focus();
    w.print();
  };
  doc.body.appendChild(frame);
  return frame;
}

/** Formats whose server documents embed chart images. */
export const IMAGE_FORMATS: ExportFormat[] = ["html", "pdf"];

/**
 * Export menu: server-side exports (streamed as a download) plus a browser print-to-PDF option
 * that uses the app's print stylesheet. `beforeExport` runs first for document formats (reports and
 * dashboards use it to upload the rendered chart images the export embeds). A PDF request on a
 * server without WeasyPrint (`409 pdf_unavailable`) falls back to printing the HTML export.
 */
export function ExportMenu({
  target,
  formats,
  filename,
  allowPrint = false,
  label = "Export",
  size = "sm",
  beforeExport,
}: {
  beforeExport?: (format: ExportFormat) => Promise<void>;
  target: ExportTarget;
  formats: ExportFormat[];
  filename: string;
  allowPrint?: boolean;
  label?: string;
  size?: "xs" | "sm";
}) {
  const { id: ws } = useWorkspace();
  const [busy, setBusy] = React.useState<ExportFormat | null>(null);
  const body = (format: ExportFormat) => ({ target: target.kind, id: target.id, format });
  const run = async (format: ExportFormat) => {
    setBusy(format);
    try {
      if (beforeExport && IMAGE_FORMATS.includes(format)) await beforeExport(format);
      await downloadFromApi(exportsApi.path(ws), `${filename}.${format}`, body(format));
    } catch (e) {
      if (format === "pdf" && e instanceof ApiError && e.code === "pdf_unavailable") {
        try {
          const res = await request<Response>(exportsApi.path(ws), { method: "POST", body: body("html"), raw: true });
          printHtml(await res.text());
          toast.info("Server PDF engine not installed", {
            description: "Opened the print dialog for the HTML export instead; choose “Save as PDF”.",
          });
        } catch (e2) {
          toast.error("PDF export failed", { description: e2 instanceof Error ? e2.message : String(e2) });
        }
      } else {
        toast.error(`${FORMAT_LABEL[format]} export failed`, { description: e instanceof Error ? e.message : String(e) });
      }
    } finally {
      setBusy(null);
    }
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" size={size} disabled={busy !== null} aria-label={`${label} options`}>
          <Download /> {busy ? `Exporting ${FORMAT_LABEL[busy]}…` : label}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>Download as</DropdownMenuLabel>
        {formats.map((f) => (
          <DropdownMenuItem key={f} onSelect={() => void run(f)}>
            {FORMAT_LABEL[f]}
          </DropdownMenuItem>
        ))}
        {allowPrint ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => setTimeout(() => window.print(), 50)}>
              <Printer /> Print / Save as PDF (browser)
            </DropdownMenuItem>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
