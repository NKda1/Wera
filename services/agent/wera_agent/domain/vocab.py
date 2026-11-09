"""Keyword normalisation (ATSC-01): a synonym map so "Postgres" counts as PostgreSQL."""

import re
from functools import lru_cache

from ..schemas.cv import Education, MasterCV, Role, Skill

# Canonical name → every spelling that counts as it, the canonical name included.
#
# Written canonical-first because that is how it reads, but **looked up from any member** — see
# `_GROUPS`. That direction is the one that matters: the keyword side of every comparison comes
# from a job post (`keywords.extract` returns the post's own wording), so the term WERA is handed
# is whichever spelling the employer happened to use. A map that only resolved its own keys was
# therefore the wrong way round in practice — "PostgreSQL" found "Postgres" in a CV, but a post
# asking for "Continuous integration" matched nothing, because the group is filed under "ci/cd".
#
# Groups are things a recruiter would accept as the same requirement, not things that are merely
# related. Docker and Kubernetes are not in one group: a CV listing Docker has not shown
# Kubernetes. Git and "version control" are, because naming the tool is how a CV says it.
SYNONYMS: dict[str, list[str]] = {
    "postgresql": ["postgresql", "postgres", "psql"],
    "llms": ["llm", "llms", "large language model", "large language models"],
    "agents": ["agent", "agents", "agentic"],
    "pii": ["pii", "personal data"],
    "rest apis": ["rest api", "rest apis", "rest", "restful", "restful apis"],
    "guardrails": ["guardrail", "guardrails"],
    "prompting": ["prompting", "prompt engineering", "prompt templates"],
    "next.js": ["next.js", "nextjs"],
    "node.js": ["node.js", "nodejs", "node api"],
    "c# / .net": ["c#", ".net", "dotnet"],
    "model evaluation": ["model evaluation", "llm evaluation", "evals"],
    "docker": ["docker", "containers", "containerisation", "containerization"],
    "rag": ["rag", "retrieval-augmented", "retrieval augmented generation"],
    "go": ["go", "golang"],
    # The groups below were added from measured misses. On a real pack the post asked for
    # "Version control", "CI/CD pipelines", "Testing", "Debugging", "Build tools", "Memory
    # management", "Multithreading" and "Relational databases"; the CV lists Git, SQL and C++ and
    # the report marked all eight unmatched *and* unbacked — so they were shown to the applicant
    # as skills to go and acquire. Naming the tool is how a CV claims the practice.
    "version control": ["version control", "git", "github", "gitlab", "bitbucket", "svn", "subversion", "mercurial"],
    "ci/cd": [
        "ci/cd",
        "cicd",
        "ci cd",
        "continuous integration",
        "continuous delivery",
        "continuous deployment",
        "github actions",
        "gitlab ci",
        "jenkins",
        "circleci",
        "travis ci",
    ],  # fmt: skip
    "relational databases": ["relational database", "relational databases", "rdbms", "sql", "mysql", "sqlite", "mariadb"],
    "testing": [
        "testing",
        "tests",
        "unit test",
        "unit tests",
        "unit testing",
        "automated test",
        "automated tests",
        "automated testing",
        "integration tests",
        "integration testing",
        "test coverage",
        "pytest",
        "jest",
        "junit",
    ],  # fmt: skip
    "debugging": ["debugging", "debug", "debugger", "fault finding", "troubleshooting"],
    "build tools": [
        "build tool",
        "build tools",
        "build system",
        "build systems",
        "webpack",
        "vite",
        "gradle",
        "maven",
        "bazel",
        "cmake",
    ],
    "memory management": ["memory management", "manual memory management", "ownership model", "raii", "garbage collection"],
    "multithreading": [
        "multithreading",
        "multi-threading",
        "concurrency",
        "concurrent",
        "concurrent programming",
        "threading",
        "threads",
        "parallelism",
        "lock-free",
        "lock free",
    ],  # fmt: skip
    "performance profiling": [
        "performance profiling",
        "profiling",
        "profiler",
        "performance tuning",
        "performance optimisation",
        "performance optimization",
    ],
    "computer science": ["computer science", "comp sci", "cs degree", "software engineering degree"],
}

# Aliases that are also ordinary English, and so only count written as the proper noun. `Go` is
# the reason this exists: lower-cased, it matches "go to the shop" in any bullet on the page.
#
# Previously this was a separate `CASE_SENSITIVE` map of whole *terms*, which worked only because
# the "go" group deliberately omitted "go" from its own alias list — so the bare word could only
# ever be reached through that map. That made the alias list wrong on its face and the mechanism
# impossible to extend. It is per-alias now: a group may mix cased and uncased spellings, so
# `golang` matches anywhere while `Go` must be capitalised.
CASE_SENSITIVE_ALIASES = frozenset({"go"})

# Qualifier tails a post hangs off a skill's name. "CI/CD pipelines", "Testing experience" and
# "Python development" ask for the same thing as the bare term, but they are not the term, so
# they missed the synonym map entirely and then matched nothing but themselves.
QUALIFIER_TAIL = re.compile(
    r"\s+(?:pipelines?|experience|skills?|knowledge|tools?|tooling|development|practices?|"
    r"frameworks?|libraries|stack|expertise|proficiency|fundamentals|principles|techniques?)$"
)


def _groups() -> dict[str, tuple[str, ...]]:
    """Every spelling → the whole group it belongs to, so a lookup works from any member."""
    out: dict[str, tuple[str, ...]] = {}
    for canonical, aliases in SYNONYMS.items():
        group = tuple(dict.fromkeys([*aliases, canonical]))
        for alias in group:
            out[alias] = group
    return out


_GROUPS = _groups()


def _reductions(key: str) -> list[str]:
    """`build tools experience` → `[build tools experience, build tools, build]`.

    One tail at a time, because some skill names *end* in a qualifier word: "build tools" is a
    thing and "build" is not. Stripping to the last possible word in one pass took "build tools
    experience" straight past its own group to "build", which matches nothing — so the chain is
    returned and the caller stops at the first step it recognises.
    """
    out = [key]
    while (stripped := QUALIFIER_TAIL.sub("", out[-1])) != out[-1]:
        out.append(stripped)
    return out


def aliases_for(term: str) -> list[str]:
    """Every spelling that counts as `term`.

    The term as written, then with its qualifier tails peeled off one at a time, stopping at the
    first form that names a known group. A term in no group at all still matches its own first
    reduction, so "Kafka experience" finds "Kafka" with nothing mapped — but only one level, since
    peeling further is guessing rather than normalising.
    """
    key = term.lower().strip()
    chain = _reductions(key)
    for form in chain:
        if group := _GROUPS.get(form):
            return list(group)
    return list(dict.fromkeys(chain[:2]))


@lru_cache(maxsize=4096)
def _alias_re(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(^|[^a-z0-9]){re.escape(alias)}($|[^a-z0-9])")


@lru_cache(maxsize=256)
def _proper_noun_re(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(^|[^A-Za-z0-9]){re.escape(alias.capitalize())}($|[^A-Za-z0-9])")


def text_has_term(text: str, term: str) -> bool:
    """Whole-word match of a term or any of its synonyms, case-insensitive except where an alias
    is also an ordinary word (`CASE_SENSITIVE_ALIASES`)."""
    haystack = text.lower()
    for alias in aliases_for(term):
        if alias in CASE_SENSITIVE_ALIASES:
            if _proper_noun_re(alias).search(text):
                return True
        elif _alias_re(alias).search(haystack):
            return True
    return False


def skill_text(skill: Skill) -> str:
    return " · ".join([skill.name, *(skill.aliases or [])])


def role_header(role: Role) -> str:
    """The line the export prints above a role's bullets (`export/content.ts`'s `row` block)."""
    return f"{role.title} — {role.org}" + (f", {role.location}" if role.location else "")


def education_text(entry: Education) -> str:
    """The line the export prints for one qualification, plus any notes under it.

    A degree subject is a keyword a post really does ask for — "Computer Science" was being
    reported as a gap to an applicant whose CV says "BA (Hons) in Computer Science" on the page
    the robot reads, because nothing here looked at `education`.
    """
    return " ".join([entry.degree, entry.institution, *[b.text for b in entry.notes or []]])


def backing_text(cv: MasterCV) -> str:
    """The master CV's factual surface, minus the prose that citations exist to anchor.

    Skills, qualifications, employers, job titles and languages — the facts a line may lean on
    without citing a bullet for them, because they are not claims about what the applicant *did*.
    Bullets and the summary are deliberately absent: those are what `source_ids` points at, and
    accepting them here would make the citation link decorative.

    `check_cited` has always had this escape hatch for the skills list alone, and the narrowness
    was a false-positive generator: a profile reading "Computer Science finalist" was failed for
    claiming a term "not in the cited lines or your skills" against a CV whose education section
    says Computer Science. A term the CV states as a fact about the applicant is backed.
    """
    return "\n".join(
        [
            *[skill_text(s) for s in cv.skills],
            *[education_text(e) for e in cv.education],
            *[role_header(r) for r in [*cv.experience, *cv.projects]],
            *[f"{lang.name} {lang.level}" for lang in cv.languages],
            cv.person.headline or "",
        ]
    )


def master_cv_text(cv: MasterCV) -> str:
    """Everything the master CV says, for "is this keyword backed by the CV?" checks."""
    bullets = [f"{b.text} {' '.join(b.tags)}" for r in [*cv.experience, *cv.projects] for b in r.bullets]
    return "\n".join(
        [
            cv.person.headline or "",
            cv.summary.text if cv.summary else "",
            *[role_header(r) for r in [*cv.experience, *cv.projects]],
            *bullets,
            *[education_text(e) for e in cv.education],
            *[f"{lang.name} {lang.level}" for lang in cv.languages],
            *[skill_text(s) for s in cv.skills],
        ]
    )


def cv_line_index(cv: MasterCV) -> dict[str, dict[str, str]]:
    """Every bullet in the CV, keyed by id, plus the summary."""
    out: dict[str, dict[str, str]] = {}
    if cv.summary:
        out[cv.summary.id] = {"text": cv.summary.text, "where": "Summary"}
    for role in [*cv.experience, *cv.projects]:
        for b in role.bullets:
            out[b.id] = {"text": b.text, "where": f"{role.title} — {role.org}"}
    for ed in cv.education:
        for b in ed.notes or []:
            out[b.id] = {"text": b.text, "where": ed.degree}
    return out
