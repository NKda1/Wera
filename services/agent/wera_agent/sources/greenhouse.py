"""Greenhouse's public board API (ANS-01).

The only thing fetched here is an application's question list, which is what an answer pack is
for. It goes through `http.py`, so the denylist and the demo-mode block apply, and a failure is
never fatal: no questions simply means no answer pack, and the CV and letter are unaffected.
"""

from typing import Any

import logfire

from ..errors import ApiError
from ..http import request
from ..schemas.jobs import Job

BOARD = "https://boards-api.greenhouse.io/v1/boards"

# Questions a board asks every applicant, which belong in the ATS form rather than a pack.
SKIP = ("first name", "last name", "email", "phone", "resume", "cover letter", "location (city)")


def _label(question: dict[str, Any]) -> str | None:
    label = (question.get("label") or "").strip()
    if not label or label.lower() in SKIP:
        return None
    return label


def fetch_questions(job: Job) -> list[str]:
    """The job's application questions, or an empty list if they can't be read."""
    if job.ats is None or job.ats.vendor != "greenhouse":
        return []
    url = f"{BOARD}/{job.ats.board_token}/jobs/{job.ats.job_id}?questions=true"
    try:
        response = request("GET", url)
        response.raise_for_status()
        body = response.json()
    except ApiError:
        raise  # demo mode and the denylist are deliberate refusals, not failures to swallow
    except Exception:  # noqa: BLE001 — a board that won't answer costs the answer pack, nothing else
        logfire.warn("Couldn't read Greenhouse questions for {job_id}", job_id=job.id)
        return []
    questions = body.get("questions") if isinstance(body, dict) else None
    if not isinstance(questions, list):
        return []
    return [label for label in (_label(q) for q in questions if isinstance(q, dict)) if label]
