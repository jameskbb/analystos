/** Finding review workflow (spec §66): Draft → Needs review → Confirmed / Rejected. */
import type { FindingStatus } from "@/lib/api/types";

export interface Transition {
  to: FindingStatus;
  label: string;
  /** A note is required (rejections must say why). */
  requiresNote: boolean;
  tone: "primary" | "secondary" | "danger";
}

const T = (to: FindingStatus, label: string, tone: Transition["tone"] = "secondary", requiresNote = false): Transition => ({
  to,
  label,
  tone,
  requiresNote,
});

export function allowedTransitions(status: FindingStatus | string): Transition[] {
  switch (status) {
    case "draft":
      return [T("needs_review", "Request review"), T("confirmed", "Confirm", "primary"), T("rejected", "Reject", "danger", true)];
    case "needs_review":
      return [T("confirmed", "Confirm", "primary"), T("rejected", "Reject", "danger", true), T("draft", "Back to draft")];
    case "confirmed":
      return [T("needs_review", "Reopen for review", "secondary", true)];
    case "rejected":
      return [T("needs_review", "Reopen for review", "secondary", true), T("draft", "Back to draft")];
    default:
      return [T("draft", "Move to draft")];
  }
}

export const STATUS_STEPS: FindingStatus[] = ["draft", "needs_review", "confirmed"];
