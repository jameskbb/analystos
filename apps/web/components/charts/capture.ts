/**
 * Chart images for exports (spec §67): report and dashboard exports embed PNGs that the browser
 * renders from the live ECharts instances. Chart containers are marked with
 * `data-capture-key` (a report block id or dashboard tile id); before an HTML/PDF export the page
 * captures every marked chart and uploads it to `POST /exports/images` with that key as
 * `source_id`, which the server matches when it builds the document.
 */
import * as echarts from "echarts/core";
import { exportsApi } from "@/lib/api/endpoints";

export interface CapturedChart {
  key: string;
  title: string;
  dataUrl: string;
}

/** Finds marked chart containers under `root` and renders each ECharts instance to a PNG data URL. */
export function captureCharts(root: ParentNode, background: string): CapturedChart[] {
  const out: CapturedChart[] = [];
  const seen = new Set<string>();
  root.querySelectorAll<HTMLElement>("[data-capture-key]").forEach((el) => {
    const key = el.dataset.captureKey;
    if (!key || seen.has(key)) return;
    const host = el.hasAttribute("_echarts_instance_") ? el : el.querySelector<HTMLElement>("[_echarts_instance_]");
    const inst = host ? echarts.getInstanceByDom(host) : undefined;
    if (!inst) return;
    const dataUrl = inst.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: background });
    if (!dataUrl.startsWith("data:image/png")) return;
    seen.add(key);
    out.push({ key, title: el.dataset.captureTitle ?? key, dataUrl });
  });
  return out;
}

/** Decodes a base64 data URL into a Blob without going through fetch(). */
export function dataUrlToBlob(dataUrl: string): Blob {
  const [head, b64] = dataUrl.split(",", 2);
  const mime = /data:([^;]+)/.exec(head)?.[1] ?? "application/octet-stream";
  const bin = atob(b64 ?? "");
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

/**
 * Captures and uploads the charts on the page. Each chart is rendered on the current theme's
 * background so its axis and label colours stay legible. Returns how many were stored.
 */
export async function uploadChartImages(
  ws: string,
  sourceType: "report_block" | "dashboard_tile",
  root: ParentNode = document,
): Promise<{ uploaded: number; failed: number }> {
  const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() || "#ffffff";
  const charts = captureCharts(root, bg);
  let failed = 0;
  await Promise.all(
    charts.map(async (c) => {
      try {
        await exportsApi.uploadImage(ws, dataUrlToBlob(c.dataUrl), `${c.key}.png`, {
          source_type: sourceType,
          source_id: c.key,
          title: c.title,
        });
      } catch {
        failed += 1;
      }
    }),
  );
  return { uploaded: charts.length - failed, failed };
}
