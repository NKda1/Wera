"""Upskills (UPSK-01…04): the skills the jobs a person is shown ask for that their CV doesn't.

Built in code from what the fit reports already hold — each scored post's keywords — so it costs
no model call and is as fresh as the last search. See `services.upskills`.
"""

from typing import Literal

from .base import Model, Opt
from .jobs import KeywordCategory


class UpskillJob(Model):
    id: str
    title: str
    company: str


class Upskill(Model):
    """One skill the CV doesn't show, and how much the person's market wants it."""

    term: str  # as the posts most often write it
    spellings: list[str] = []  # every way the posts wrote it, so a pack's gap finds its card
    category: Opt[KeywordCategory] = None
    jobs: int  # relevant jobs asking for it
    share: float  # of the relevant jobs, 0–1
    required_share: float  # of the jobs asking, the share that mark it required
    # Demand weighted by how much each job matters to the person: required counts 2, a job they
    # shortlisted or applied to counts double. What the list is ordered by.
    score: float
    trend: Literal["new", "rising", "steady", "falling"]
    recent: int  # jobs asking in the last 14 days
    previous: int  # in the 14 before
    close_to: list[str]  # related skills the CV does show — the shortest way in
    examples: list[UpskillJob]


class QuickWin(Model):
    """A skill the posts ask for that the CV backs in a line but doesn't list as a skill: the
    cheapest point on the ATS score (ATSC-07), and only the person can decide to claim it."""

    term: str
    jobs: int
    share: float
    line: str  # where on the CV it already appears


class UpskillsReport(Model):
    window_days: int
    jobs_considered: int
    missing: list[Upskill]
    quick_wins: list[QuickWin]
