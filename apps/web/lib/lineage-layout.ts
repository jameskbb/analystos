/** Deterministic layered layout for lineage DAGs (left → right: conclusion → source). */
import type { LineageGraph } from "@/lib/api/types";

export interface PositionedNode {
  id: string;
  layer: number;
  order: number;
  x: number;
  y: number;
}

export interface LineageLayout {
  nodes: Map<string, PositionedNode>;
  width: number;
  height: number;
  layers: number;
}

export function layoutLineage(
  graph: LineageGraph,
  opts: { nodeWidth: number; nodeHeight: number; gapX: number; gapY: number },
): LineageLayout {
  const ids = graph.nodes.map((n) => n.id);
  const idSet = new Set(ids);
  const edges = graph.edges.filter((e) => idSet.has(e.from) && idSet.has(e.to) && e.from !== e.to);
  const incoming = new Map<string, string[]>(ids.map((id) => [id, []]));
  for (const e of edges) incoming.get(e.to)!.push(e.from);

  // Longest path from sources, with cycle protection.
  const layer = new Map<string, number>();
  const visiting = new Set<string>();
  const depth = (id: string): number => {
    if (layer.has(id)) return layer.get(id)!;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const parents = incoming.get(id) ?? [];
    const d = parents.length ? Math.max(...parents.map(depth)) + 1 : 0;
    visiting.delete(id);
    layer.set(id, d);
    return d;
  };
  ids.forEach(depth);

  const byLayer = new Map<number, string[]>();
  ids.forEach((id) => {
    const l = layer.get(id) ?? 0;
    const arr = byLayer.get(l);
    if (arr) arr.push(id);
    else byLayer.set(l, [id]);
  });

  // Order within layers by the mean order of parents (one barycenter sweep keeps crossings low).
  const order = new Map<string, number>();
  const layerKeys = [...byLayer.keys()].sort((a, b) => a - b);
  for (const l of layerKeys) {
    const list = byLayer.get(l)!;
    if (l > 0) {
      const bary = (id: string) => {
        const ps = (incoming.get(id) ?? []).map((p) => order.get(p) ?? 0);
        return ps.length ? ps.reduce((s, v) => s + v, 0) / ps.length : 0;
      };
      list.sort((a, b) => bary(a) - bary(b));
    }
    list.forEach((id, i) => order.set(id, i));
  }

  const maxRows = Math.max(1, ...[...byLayer.values()].map((l) => l.length));
  const height = maxRows * opts.nodeHeight + (maxRows - 1) * opts.gapY;
  const nodes = new Map<string, PositionedNode>();
  for (const l of layerKeys) {
    const list = byLayer.get(l)!;
    const colHeight = list.length * opts.nodeHeight + (list.length - 1) * opts.gapY;
    const top = (height - colHeight) / 2;
    list.forEach((id, i) => {
      nodes.set(id, {
        id,
        layer: l,
        order: i,
        x: l * (opts.nodeWidth + opts.gapX),
        y: top + i * (opts.nodeHeight + opts.gapY),
      });
    });
  }
  const layers = layerKeys.length;
  return { nodes, width: layers * opts.nodeWidth + Math.max(0, layers - 1) * opts.gapX, height, layers };
}
