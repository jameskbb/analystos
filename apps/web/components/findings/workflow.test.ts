import { describe, expect, it } from "vitest";
import { allowedTransitions } from "./workflow";

describe("finding workflow", () => {
  it("lets drafts be reviewed, confirmed or rejected", () => {
    expect(allowedTransitions("draft").map((t) => t.to)).toEqual(["needs_review", "confirmed", "rejected"]);
  });
  it("requires a note to reject or reopen", () => {
    expect(allowedTransitions("needs_review").find((t) => t.to === "rejected")!.requiresNote).toBe(true);
    expect(allowedTransitions("confirmed")).toEqual([expect.objectContaining({ to: "needs_review", requiresNote: true })]);
  });
  it("never offers a transition to the current status", () => {
    for (const s of ["draft", "needs_review", "confirmed", "rejected"]) {
      expect(allowedTransitions(s).some((t) => t.to === s)).toBe(false);
    }
  });
});
