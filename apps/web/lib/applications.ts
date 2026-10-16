// Application lifecycle (SPEC §7.2). Shared by the server and the tracker UI.

import type { ApplicationStatus } from "./types";

/** Transitions a person can make from the tracker. Approval only happens at Gate 2. */
export const ALLOWED: Record<ApplicationStatus, ApplicationStatus[]> = {
  drafting: ["discarded"],
  changes_requested: ["discarded"],
  ready_for_review: ["discarded"],
  approved: ["submitted", "discarded"],
  submitted: ["interview", "rejected", "withdrawn"],
  interview: ["offer", "rejected", "withdrawn"],
  offer: ["withdrawn"],
  rejected: [],
  discarded: [],
  withdrawn: [],
};

export const STATUS_LABEL: Record<ApplicationStatus, string> = {
  drafting: "Drafting",
  changes_requested: "Redrafting",
  ready_for_review: "To review",
  approved: "Approved",
  submitted: "Submitted",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  discarded: "Discarded",
  withdrawn: "Withdrawn",
};
