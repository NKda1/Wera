"""Upskills (UPSK-01…06): the skills the jobs a person is shown ask for that their CV doesn't.

Built in code from what the fit reports already hold — each scored post's keywords — and, since
20 Nov, from the jobs the CV nearly matched (`job_signals`), so it costs no model call to read and
is as fresh as the last search. See `services.upskills`.
"""

from typing import Literal

from .base import Model, Opt
from .jobs import KeywordCategory


class UpskillJob(Model):
    id: str
    title: str
    company: str


class NearMissJob(Model):
    """A job the CV nearly matched (UPSK-06): a title the filter dropped, read for what it asks.
    `job_id` when it is a saved (hidden) job; else `url`, the API's link to it."""

    title: str
    company: str
    url: Opt[str] = None
    job_id: Opt[str] = None


class FamilyShare(Model):
    """How many jobs of one role family (UPSK-07, 21 Nov): "Quantitative roles", 6."""

    family: str
    label: str
    jobs: int  # jobs on the list of this family (near misses in `near_misses`)
    near_misses: int = 0


class Upskill(Model):
    """One skill the CV doesn't show, and how much the person's market wants it."""

    term: str  # as the posts most often write it
    spellings: list[str] = []  # every way the posts wrote it, so a pack's gap finds its card
    category: Opt[KeywordCategory] = None
    jobs: int  # jobs on the person's list (scored) asking for it
    # UPSK-06 (20 Nov): jobs the CV nearly matched that ask for it. 0 in the list without them.
    near_misses: int = 0
    share: float  # of the scored jobs, 0–1
    required_share: float  # of the jobs asking (near misses included), the share that mark it required
    # Demand weighted by how much each job matters to the person: required counts 2, a job they
    # shortlisted or applied to counts double, a near miss half — and, since 20 Nov, a job the CV
    # matched poorly up to twice (`upskills.lift`): its gaps say most about what to improve. What
    # the list is ordered by; never a count of jobs.
    score: float
    # 20 Nov: of the jobs asking (near misses included), those the CV matched poorly — a fit or
    # ATS score of 6 or less, a similarity of 0.80 or less (`upskills.POOR`). The page marks a gap
    # "mostly from jobs you matched poorly" when they are more than half.
    poor: int = 0
    trend: Literal["new", "rising", "steady", "falling"]
    recent: int  # jobs asking in the last 14 days
    previous: int  # in the 14 before
    close_to: list[str]  # related skills the CV does show — the shortest way in
    examples: list[UpskillJob]
    near_miss_examples: list[NearMissJob] = []
    # UPSK-07 (21 Nov): which kinds of role ask for it, most first, and whether they are mostly
    # outside the person's own field — C++ for a finance CV, asked by quantitative roles.
    families: list[FamilyShare] = []
    outside_field: bool = False


class QuickWin(Model):
    """A skill the posts ask for that the CV backs in a line but doesn't list as a skill: the
    cheapest point on the ATS score (ATSC-07), and only the person can decide to claim it."""

    term: str
    category: Opt[KeywordCategory] = None
    jobs: int
    share: float
    line: str  # where on the CV it already appears
    # 20 Nov: the near misses asking (0 in the view without them), and the importance the list is
    # ordered by — each job's `upskills.lift`, a near miss at half (see `SkillDemand.importance`).
    near_misses: int = 0
    importance: float = 0.0


# Where a skill the posts ask for stands against the CV: in its skills list, in a line but not
# the list (a quick win), or nowhere on it.
type SkillStatus = Literal["covered", "quick_win", "missing"]


class SkillDemand(Model):
    """One skill the posts ask for, whatever the CV says about it — what the Upskills chart of
    the most-asked skills draws, coloured by `status` (17 Nov).

    20 Nov: each row says where its demand comes from — `jobs` on the list and `near_misses` the CV
    nearly matched, plain counts that always add up to the jobs asking — and how much it matters:
    `importance` from the jobs on the list, `near_importance` from the near misses. A job weighs
    its `upskills.lift` (1 for a strong match, up to 2 for a poor one) and a near miss half that,
    except for a skill the CV lists (`covered`), which weighs its plain count: a skill already
    on the CV isn't what a poor match teaches. The chart draws and orders by the two
    importances, stacked; its labels and tooltips give the counts."""

    term: str
    category: Opt[KeywordCategory] = None
    jobs: int
    # UPSK-06 (20 Nov): near misses asking for it; 0 in the view without them.
    near_misses: int = 0
    share: float  # `jobs` of the scored jobs, 0–1: near misses never change it
    importance: float = 0.0
    near_importance: float = 0.0
    poor: int = 0  # of `jobs` + `near_misses`, those the CV matched poorly (see `Upskill.poor`)
    status: SkillStatus


class Coverage(Model):
    """Skill asks — one job asking for one skill — by where the CV stands on them. Counted over
    asks rather than distinct skills, so a skill twenty jobs want weighs twenty times one that a
    single job mentions.

    20 Nov: `covered`, `quick_wins` and `missing` are the asks of jobs on the list, as before; the
    `near_*` three are the near misses' asks, plain counts too (0 in the view without them). The
    page draws a near-miss ask as `NearMissView.near_weight` (0.5) of an ask — the weight the gap
    list gives a near miss — so the donut's shares are (list + 0.5 × near) over the same sum, and
    its labels say both counts. Coverage is not lifted for poorly matched jobs: it answers "how
    much of what's asked does the CV show", which an inflated gap would misstate."""

    covered: int = 0
    quick_wins: int = 0
    missing: int = 0
    near_covered: int = 0
    near_quick_wins: int = 0
    near_missing: int = 0


class CategoryCoverage(Coverage):
    category: KeywordCategory | Literal["other"]


class LevelDemand(Model):
    """How many of the jobs asking for a degree ask for this level, and whether the CV has it."""

    level: Literal["bachelor", "master", "phd"]
    jobs: int
    share: float  # of the jobs that ask for any degree
    met: bool  # the CV's education is at this level or above


class Programme(Model):
    """A programme employers in the person's field name: from the postings, counted, or from the
    starter list (`config/degrees.yaml`) while the postings have named too few."""

    name: str
    mentions: int  # postings naming it; 0 on the starter list
    source: Literal["postings", "starter"]


class EducationDemand(Model):
    """UPSK-05 (20 Nov): the degrees the jobs ask for against the CV's education. Degrees are
    judged here, in code (`domain.degrees`), and never listed as skills."""

    cv_level: str  # the highest level the CV shows: none, bachelor, master or phd
    jobs_asking: int  # jobs that name a degree level or subject
    met: int
    level_gaps: int
    subject_gaps: int
    levels: list[LevelDemand] = []
    # The programmes above the CV's level that the field asks for, most named first: the
    # direction a bachelor's graduate weighing a master's is looking for.
    programmes: list[Programme] = []
    field: Opt[str] = None
    gap_examples: list[UpskillJob] = []
    # UPSK-06: of `jobs_asking`, the near misses (0 in the view without them).
    near_misses: int = 0


class NearMissView(Model):
    """UPSK-06 (20 Nov): the gaps and the degree asks with the jobs the CV nearly matched counted
    in — at half a scored job's weight — beside the report's own, which count scored jobs only. The
    page's "Include near misses" switch picks between them.

    Amended 20 Nov: the view carries its own chart data and quick wins too, under the report's
    own field names, so the page reads either view the same way and the switch moves the charts
    as well as the table (the user, 20 Nov: "the bar charts and pie charts" didn't move while the
    table's "nearly matched" counts did). `jobs_considered` is the scored jobs, as on the report —
    a near miss is never called a job on the list — and `near_misses` how many were read."""

    near_misses: int  # near misses read in the window
    missing: list[Upskill]
    education: Opt[EducationDemand] = None
    jobs_considered: int = 0
    near_weight: float = 0.5  # what one near-miss ask counts as on the charts (`upskills.near_miss_weight`)
    quick_wins: list[QuickWin] = []
    demand: list[SkillDemand] = []
    coverage: Coverage = Coverage()
    by_category: list[CategoryCoverage] = []


class UpskillsReport(Model):
    window_days: int
    jobs_considered: int
    missing: list[Upskill]
    quick_wins: list[QuickWin]
    # 17 Nov: the market against the CV as a whole, for the page's charts. Defaulted so a report
    # built before these existed still reads.
    demand: list[SkillDemand] = []
    coverage: Coverage = Coverage()
    by_category: list[CategoryCoverage] = []
    education: Opt[EducationDemand] = None
    # UPSK-07 (21 Nov): the person's own role family (from their target titles), every family the
    # window's jobs fall in for the page's filter, and the one the report is filtered to, if any.
    own_family: Opt[str] = None
    families: list[FamilyShare] = []
    family: Opt[str] = None
    # UPSK-06: absent when no near miss was read in the window.
    with_near_misses: Opt[NearMissView] = None
    # 22 Nov: what the report was built from (`upskills.version`). The page compares it with
    # `GET /upskills/version` and refreshes when a search, a near miss or a decision moves it.
    version: str = ""


class UpskillsVersion(Model):
    version: str
