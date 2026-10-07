"use client";
import * as React from "react";
import type { Bridge } from "@/lib/api/types";
import { formatCompact, formatSignedChange, formatValue } from "@/lib/format";
import { bridgeBars, bridgeGroups } from "./bridge";

const COL = 104;
const TOP = 24;
const LABELS = 44;
const GROUPS = 20;

/** Split a label into at most two lines of roughly `max` characters, for the axis under each bar. */
export function wrapLabel(label: string, max = 16): string[] {
  const words = label.split(/\s+/);
  const lines: string[] = [""];
  for (const w of words) {
    const cur = lines[lines.length - 1];
    if (!cur) lines[lines.length - 1] = w;
    else if (`${cur} ${w}`.length <= max) lines[lines.length - 1] = `${cur} ${w}`;
    else if (lines.length < 2) lines.push(w);
    else {
      lines[1] = `${lines[1]} ${w}`;
    }
  }
  if (lines[1] && lines[1].length > max) lines[1] = `${lines[1].slice(0, max - 1)}…`;
  return lines;
}

/**
 * The plan-to-actual waterfall: the plan total, one floating bar per reconciled effect (red lowers the
 * metric, green raises it), and the actual total. Every value is the engine's; a bar with a node selects it.
 */
export function BridgeChart({
  bridge,
  onSelect,
  height = 210,
  className,
  column = COL,
  wrapAt = 16,
}: {
  bridge: Bridge;
  onSelect?: (nodeId: string) => void;
  height?: number;
  className?: string;
  /** Width of one bar's column in view units; narrower columns suit narrow pages (the memo). */
  column?: number;
  /** Characters per axis-label line. */
  wrapAt?: number;
}) {
  const BAR = Math.round(column * 0.54);
  const bars = bridgeBars(bridge);
  const groups = bridgeGroups(bridge);
  const fmt = bridge.metric_format ?? "currency";
  const width = bars.length * column;
  const plotH = height - TOP - LABELS - (groups.length ? GROUPS : 0);
  const lo = Math.min(0, ...bars.flatMap((b) => [b.from, b.to]));
  const hi = Math.max(0, ...bars.flatMap((b) => [b.from, b.to]));
  const span = hi - lo || 1;
  const y = (v: number) => TOP + ((hi - v) / span) * plotH;
  const x = (i: number) => i * column + (column - BAR) / 2;
  return (
    <figure className={className} data-testid="bridge-chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height={height}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`${bridge.metric_label}: ${bridge.start_label} ${formatValue(bridge.start, fmt, { compact: true })}, ${bridge.steps
          .map((s) => `${s.label} ${formatSignedChange(s.amount, fmt)}`)
          .join(", ")}, ${bridge.end_label} ${formatValue(bridge.end, fmt, { compact: true })}`}
        className="overflow-visible"
      >
        <line x1={0} x2={width} y1={y(0)} y2={y(0)} stroke="var(--border-strong)" strokeWidth={1} />
        {bars.map((b, i) => {
          const top = y(Math.max(b.from, b.to));
          const h = Math.max(1, Math.abs(y(b.from) - y(b.to)));
          const fill = b.kind === "total" ? (i === 0 ? "var(--fg-subtle)" : "var(--accent)") : b.amount < 0 ? "var(--negative)" : "var(--positive)";
          const next = bars[i + 1];
          const clickable = b.kind === "step" && !!b.node_id && !!onSelect;
          const text = b.kind === "total" ? formatCompact(b.amount, fmt) : formatSignedChange(b.amount, fmt);
          const lines = wrapLabel(b.label, wrapAt);
          return (
            <g
              key={`${i}-${b.label}`}
              data-bar={b.kind}
              data-amount={b.amount}
              data-node-id={b.node_id ?? undefined}
              onClick={clickable ? () => onSelect!(b.node_id!) : undefined}
              onKeyDown={clickable ? (e) => (e.key === "Enter" || e.key === " ") && onSelect!(b.node_id!) : undefined}
              role={clickable ? "button" : undefined}
              tabIndex={clickable ? 0 : undefined}
              aria-label={clickable ? `${b.label} ${text}: open the node` : undefined}
              className={clickable ? "cursor-pointer outline-none [&:focus-visible>rect]:stroke-[var(--ring)] [&:hover>rect]:opacity-80" : undefined}
            >
              <rect x={x(i)} y={top} width={BAR} height={h} rx={2} fill={fill} strokeWidth={2} />
              <text
                x={x(i) + BAR / 2}
                y={top - 6}
                textAnchor="middle"
                fontSize={12}
                fontWeight={b.kind === "total" ? 700 : 600}
                fill={b.kind === "step" ? (b.amount < 0 ? "var(--negative)" : "var(--positive)") : "var(--fg)"}
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {text}
              </text>
              {lines.map((l, k) => (
                <text key={k} x={x(i) + BAR / 2} y={TOP + plotH + 16 + k * 13} textAnchor="middle" fontSize={11} fill="var(--fg-muted)">
                  {l}
                </text>
              ))}
              {next ? (
                <line
                  x1={x(i) + BAR}
                  x2={x(i + 1)}
                  y1={y(b.to)}
                  y2={y(b.to)}
                  stroke="var(--fg-faint)"
                  strokeDasharray="3 3"
                  strokeWidth={1}
                />
              ) : null}
            </g>
          );
        })}
        {groups.map((g) => {
          // Steps sit at bar indexes 1..n (index 0 is the start total).
          const x1 = x(g.first + 1) - 4;
          const x2 = x(g.last + 1) + BAR + 4;
          const gy = TOP + plotH + LABELS;
          return (
            <g key={`${g.group}-${g.first}`} data-group={g.group}>
              <line x1={x1} x2={x2} y1={gy} y2={gy} stroke="var(--border-strong)" strokeWidth={1} />
              <text x={(x1 + x2) / 2} y={gy + 14} textAnchor="middle" fontSize={11} fontWeight={600} fill="var(--fg-subtle)">
                {g.group}
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

/** The same bridge as a table: the reading form for the memo, print and screen readers. */
export function BridgeTable({ bridge, onSelect }: { bridge: Bridge; onSelect?: (nodeId: string) => void }) {
  const fmt = bridge.metric_format ?? "currency";
  return (
    <table className="w-full text-sm" aria-label={`${bridge.metric_label} bridge from ${bridge.start_label.toLowerCase()} to ${bridge.end_label.toLowerCase()}`}>
      <tbody className="tabular">
        <tr className="border-b border-border font-semibold">
          <td className="py-1 pr-2">
            {bridge.metric_label}, {bridge.start_label.toLowerCase()}
          </td>
          <td className="py-1 text-right">{formatValue(bridge.start, fmt)}</td>
        </tr>
        {bridge.steps.map((s, i) => (
          <tr key={i} className="border-b border-border" data-step={s.label}>
            <td className="py-1 pr-2 pl-3">
              {s.node_id && onSelect ? (
                <button type="button" className="text-left hover:text-accent hover:underline" onClick={() => onSelect(s.node_id!)}>
                  {s.label}
                </button>
              ) : (
                s.label
              )}
              {s.group_label && s.group_label !== s.label ? <span className="text-xs text-fg-subtle"> · {s.group_label}</span> : null}
            </td>
            <td className={s.amount < 0 ? "py-1 text-right text-negative" : "py-1 text-right text-positive"}>{formatSignedChange(s.amount, fmt)}</td>
          </tr>
        ))}
        <tr className="font-semibold">
          <td className="py-1 pr-2">
            {bridge.metric_label}, {bridge.end_label.toLowerCase()}
          </td>
          <td className="py-1 text-right">{formatValue(bridge.end, fmt)}</td>
        </tr>
      </tbody>
    </table>
  );
}
