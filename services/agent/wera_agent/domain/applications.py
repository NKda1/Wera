"""Application lifecycle (SPEC §7.2). The web app keeps a copy for the tracker UI."""

ALLOWED: dict[str, list[str]] = {
    # Transitions a person can make from the tracker. Approval only happens at Gate 2.
    "drafting": ["discarded"],
    "changes_requested": ["discarded"],
    "ready_for_review": ["discarded"],
    "approved": ["submitted", "discarded"],
    "submitted": ["interview", "rejected", "withdrawn"],
    "interview": ["offer", "rejected", "withdrawn"],
    "offer": ["withdrawn"],
    "rejected": [],
    "discarded": [],
    "withdrawn": [],
}
