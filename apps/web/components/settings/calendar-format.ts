/** Pure helpers for the calendar settings preview. */
import type { CalendarResolution, TimeWindow } from "@/lib/api/types";
import { formatWindow } from "@/lib/format";

export const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;

export const PREVIEW_EXPRESSIONS = ["last month", "this month", "Q3", "YTD", "rolling 30 days", "August"] as const;

/** Inclusive day count of a window (dates are ISO `YYYY-MM-DD`, possibly with a time part). */
export function windowDays(w: Pick<TimeWindow, "start" | "end"> | null | undefined): number | null {
  if (!w?.start || !w?.end) return null;
  const s = Date.parse(w.start.slice(0, 10) + "T00:00:00Z");
  const e = Date.parse(w.end.slice(0, 10) + "T00:00:00Z");
  if (Number.isNaN(s) || Number.isNaN(e)) return null;
  return Math.round((e - s) / 86_400_000) + 1;
}

export function describeWindow(w: TimeWindow | null | undefined): string {
  if (!w) return "n/a";
  const days = windowDays(w);
  return `${formatWindow(w.start?.slice(0, 10), w.end?.slice(0, 10))}${days ? ` (${days} ${days === 1 ? "day" : "days"})` : ""}`;
}

export function describeResolution(r: CalendarResolution): { window: string; previous: string; yoy: string } {
  return {
    window: describeWindow(r.window),
    previous: describeWindow(r.previous_period),
    yoy: describeWindow(r.same_period_last_year),
  };
}

/** "Fiscal year runs October – September" style label. */
export function fiscalYearLabel(startMonth: number): string {
  const s = Math.min(Math.max(Math.round(startMonth), 1), 12);
  if (s === 1) return "Fiscal year matches the calendar year (January – December)";
  const end = MONTHS[(s + 10) % 12];
  return `Fiscal year runs ${MONTHS[s - 1]} – ${end}`;
}
