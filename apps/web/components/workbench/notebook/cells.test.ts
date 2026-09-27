import { describe, expect, it } from "vitest";
import type { NotebookCell } from "@/lib/api/types";
import { dataSourceCells, insertPosition, moveCellIds, nextCellId, runAllOutcome, sortCells, staleCellIds } from "./cells";

const cell = (id: string, position: number, kind: NotebookCell["kind"] = "sql", extra: Partial<NotebookCell> = {}): NotebookCell => ({
  id,
  position,
  kind,
  source: "",
  config: {},
  status: "ok",
  execution_count: 1,
  ...extra,
});

describe("notebook cell helpers", () => {
  const cells = [cell("c", 2, "chart"), cell("a", 0), cell("b", 1, "markdown"), cell("d", 3, "python")];

  it("sorts by position", () => {
    expect(sortCells(cells).map((c) => c.id)).toEqual(["a", "b", "c", "d"]);
  });

  it("moves cells up and down, ignoring edges", () => {
    expect(moveCellIds(["a", "b", "c"], "b", -1)).toEqual(["b", "a", "c"]);
    expect(moveCellIds(["a", "b", "c"], "b", 1)).toEqual(["a", "c", "b"]);
    expect(moveCellIds(["a", "b", "c"], "a", -1)).toEqual(["a", "b", "c"]);
    expect(moveCellIds(["a", "b", "c"], "c", 1)).toEqual(["a", "b", "c"]);
  });

  it("computes insertion positions and the next cell", () => {
    expect(insertPosition(cells, "b")).toBe(2);
    expect(insertPosition(cells, null)).toBe(4);
    expect(nextCellId(cells, "b")).toBe("c");
    expect(nextCellId(cells, "d")).toBeNull();
  });

  it("offers only earlier SQL/Python cells as data sources", () => {
    expect(dataSourceCells(cells, "c").map((c) => c.id)).toEqual(["a"]);
    expect(dataSourceCells(cells, "d").map((c) => c.id)).toEqual(["a"]);
  });

  it("marks edited, never-run and downstream cells stale", () => {
    const nb = [cell("a", 0), cell("b", 1, "markdown"), cell("c", 2, "chart"), cell("d", 3, "python", { execution_count: 0 })];
    expect([...staleCellIds(nb, new Set(["a"]))]).toEqual(["a", "c", "d"]);
    expect([...staleCellIds(nb, new Set())]).toEqual(["d"]);
    const failed = [cell("a", 0, "sql", { status: "error" }), cell("b", 1)];
    expect([...staleCellIds(failed, new Set())]).toEqual(["b"]);
  });
});

describe("runAllOutcome", () => {
  const nb = (cells: NotebookCell[]) => ({ id: "nb1", title: "N", cells, created_at: "2026-09-01T00:00:00Z" });
  it("reports success from the RunAllResult wrapper, not a bare notebook", () => {
    const res = { notebook: nb([cell("a", 0), cell("b", 1)]), executed: 2, stopped_at: null };
    expect(runAllOutcome(res)).toEqual({ ok: true, executed: 2 });
  });
  it("uses stopped_at to name the failing cell and its error", () => {
    const failing = cell("b", 1, "python", { status: "error", output: { error: "ZeroDivisionError" } as NotebookCell["output"] });
    const res = { notebook: nb([cell("c", 2), failing, cell("a", 0)]), executed: 2, stopped_at: "b" };
    expect(runAllOutcome(res)).toEqual({ ok: false, executed: 2, index: 2, error: "ZeroDivisionError" });
  });
});
