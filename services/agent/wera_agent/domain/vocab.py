"""Keyword normalisation (ATSC-01): a synonym map so "Postgres" counts as PostgreSQL."""

import re
import unicodedata
from functools import lru_cache
from typing import Protocol

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
    # ATSC-07 (14 Nov): the spellings recruiters and parsers treat as one requirement, from the
    # 175 fit reports' keywords — "gcp" and "google cloud platform" were counted as two missing
    # skills, and an ad saying "Amazon Web Services" didn't match a CV saying AWS.
    "aws": ["aws", "amazon web services"],
    # 20 Nov, from the finance CV's Upskills: "discounted cash flow" was a gap to a CV built on "DCF
    # valuations", "comps" to one that ran "comparable company … benchmarking", and "Excel
    # modeling" to one that lists Excel. Finance writes its methods as abbreviations, and a CV and a
    # post rarely pick the same one.
    "discounted cash flow": ["discounted cash flow", "dcf", "dcf valuation", "dcf valuations", "dcf model", "dcf models"],
    "comparable company analysis": [
        "comparable company analysis",
        "comparable companies",
        "comparable company",
        "comps",
        "trading comps",
        "trading comparables",
        "comparables",
    ],  # fmt: skip
    "leveraged buyout": ["leveraged buyout", "leveraged buyouts", "lbo", "lbo model", "lbo models", "lbo modeling"],
    "mergers and acquisitions": ["mergers and acquisitions", "mergers & acquisitions", "m&a"],
    "financial modeling": ["financial modeling", "financial model", "financial models", "excel modeling", "3-statement model"],
    "microsoft excel": ["microsoft excel", "ms excel", "excel"],
    "microsoft word": ["microsoft word", "ms word"],
    "microsoft powerpoint": ["microsoft powerpoint", "ms powerpoint", "powerpoint"],
    "equity research": ["equity research", "equity analysis", "stock research"],
    "pitch books": ["pitch books", "pitch book", "pitchbook", "pitchbooks", "pitch decks"],
    "gcp": ["gcp", "google cloud", "google cloud platform"],
    "azure": ["azure", "microsoft azure"],
    "javascript": ["javascript", "js", "ecmascript"],
    "typescript": ["typescript"],
    "kubernetes": ["kubernetes", "k8s"],
    "machine learning": ["machine learning", "ml"],
    "artificial intelligence": ["artificial intelligence", "ai"],
    "c++": ["c++", "cpp"],
    "react": ["react", "react.js", "reactjs"],
    "vue": ["vue", "vue.js", "vuejs"],
    "object-oriented programming": [
        "object-oriented programming",
        "object oriented programming",
        "oop",
        "object-oriented design",
    ],
    "data structures and algorithms": ["data structures and algorithms", "data structures & algorithms", "dsa"],
    "apis": ["api", "apis"],
    "microservices": ["microservices", "microservice", "micro-services", "microservice architecture"],
    "distributed systems": ["distributed systems", "distributed system", "distributed computing"],
    "nlp": ["nlp", "natural language processing"],
    "deep learning": ["deep learning", "neural network", "neural networks"],
    "pytorch": ["pytorch", "torch"],
    "scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    # Naming the tool is how a CV claims the practice, as with CI/CD above.
    "observability": ["observability", "monitoring", "prometheus", "grafana", "opentelemetry", "datadog"],
}

# Aliases that are also ordinary English, and so only count written as the proper noun. `Go` is
# the reason this exists: lower-cased, it matches "go to the shop" in any bullet on the page.
#
# Previously this was a separate `CASE_SENSITIVE` map of whole *terms*, which worked only because
# the "go" group deliberately omitted "go" from its own alias list — so the bare word could only
# ever be reached through that map. That made the alias list wrong on its face and the mechanism
# impossible to extend. It is per-alias now: a group may mix cased and uncased spellings, so
# `golang` matches anywhere while `Go` must be capitalised.
#
# Each maps to the one written form that counts, because "capitalised" is not one rule: the
# language is `Go` but the architecture is `REST`, and "Rest of the migration" at the start of a
# bullet is exactly the false positive this exists to stop.
CASE_SENSITIVE_ALIASES: dict[str, str] = {
    # 20 Nov: "candidates who excel under pressure" is not a spreadsheet.
    "excel": "Excel",
    "go": "Go",
    "rest": "REST",
    "rag": "RAG",
    "jest": "Jest",
    "maven": "Maven",
    # 14 Nov: short forms that are ordinary letters or words in lower case ("ml" of water, "ai" in
    # a name, "js" in a file path) only count as the acronym.
    "ml": "ML",
    "ai": "AI",
    "js": "JS",
    "oop": "OOP",
    "dsa": "DSA",
    "torch": "Torch",
}

# Aliases that are ordinary words with no cased form to tell them apart, so they count only when
# the post itself wrote them — never as a synonym reached from another term. Measured: "Moved
# shipping containers" covered a post asking for Docker, and "ran blood tests" covered Testing.
# WERA reads CVs from every field, not only engineering ones, so a word that is a skill in one
# trade is ordinary in another. A post that literally asks for "tests" still matches "tests".
#
# Not "agent": the plural is the group's own name and matches "estate agents" either way, so
# making the singular ambiguous protected nothing and cost the match between "agent tooling" in
# a post and a post's own "Agents".
AMBIGUOUS_ALIASES = frozenset({"tests", "containers", "threads", "threading", "concurrent", "profiling", "monitoring", "torch"})

# Qualifier tails a post hangs off a skill's name. "CI/CD pipelines", "Testing experience" and
# "Python development" ask for the same thing as the bare term, but they are not the term, so
# they missed the synonym map entirely and then matched nothing but themselves.
QUALIFIER_TAIL = re.compile(
    r"\s+(?:pipelines?|experience|skills?|knowledge|tools?|tooling|development|practices?|"
    r"frameworks?|libraries|stack|expertise|proficiency|fundamentals|principles|techniques?)$"
)


# Typography a post or a CV may use for the same character. "Bachelor’s Degree" (U+2019) slipped
# past `NOT_A_SKILL`'s "bachelor's degree" on 20 Nov and was shown as a gap to a CV with a BSc:
# every comparison here was an exact string test on whatever the page typed.
_TYPOGRAPHY = str.maketrans(
    {
        "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'", "\u00b4": "'", "\u0060": "'",
        "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
        "\u00a0": " ", "\u202f": " ", "\u2009": " ",
    }
)  # fmt: skip


# British and American spellings of the same word, written American-side so both sides of a
# comparison meet: "Financial Modeling" in a post was a gap to "financial modelling" on a CV (20 Nov).
# Only whole words, and only words that mean one thing — not "program", which is also code.
_SPELLINGS = {
    "modelling": "modeling", "modelled": "modeled", "labelling": "labeling", "travelling": "traveling",
    "analyse": "analyze", "analysed": "analyzed", "analysing": "analyzing", "analyser": "analyzer",
    "optimise": "optimize", "optimised": "optimized", "optimising": "optimizing", "optimisation": "optimization",
    "organisation": "organization", "organisations": "organizations", "organisational": "organizational",
    "visualisation": "visualization", "visualise": "visualize", "standardisation": "standardization",
    "prioritise": "prioritize", "prioritisation": "prioritization", "summarise": "summarize",
    "synthesise": "synthesize", "synthesised": "synthesized", "utilise": "utilize", "utilisation": "utilization",
    "behaviour": "behavior", "behavioural": "behavioral", "colour": "color", "centre": "center",
    "catalogue": "catalog", "licence": "license", "defence": "defense", "programme": "program",
    "programmes": "programs", "amortisation": "amortization", "capitalisation": "capitalization",
    "monetisation": "monetization", "securitisation": "securitization", "localisation": "localization",
}  # fmt: skip
_SPELLING_RE = re.compile(r"\b(" + "|".join(sorted(_SPELLINGS, key=len, reverse=True)) + r")\b")


def _one_spelling(lowered: str) -> str:
    return _SPELLING_RE.sub(lambda m: _SPELLINGS[m.group(1)], lowered)


@lru_cache(maxsize=1024)
def normalise_text(text: str) -> str:
    """The text with one spelling per character, case kept: NFKC (ligatures, full-width forms),
    curly quotes and primes made straight, dashes made "-", every space one space.

    Applied to both sides of every comparison in this module, so a post's typography can never
    decide whether a term matches. Case is kept for `CASE_SENSITIVE_ALIASES`; callers lower it."""
    return " ".join(unicodedata.normalize("NFKC", text).translate(_TYPOGRAPHY).split())


def normalise_term(term: str) -> str:
    """`normalise_text`, lower-cased and in one spelling (`_SPELLINGS`): the form every lookup key
    here is written in."""
    return _one_spelling(normalise_text(term).lower())


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


# Not a skill a CV can claim or a course can teach: ways of working, generic traits, and fields of
# study. Kept out of Upskills (UPSK-01) and out of a pack's suggested skills (ATSC-07) — a pack
# suggested "Computer Science" and "Engineering" as skills to add on 14 Nov.
NOT_A_SKILL = frozenset(
    {
        "problem solving", "problem-solving", "communication", "communication skills", "written communication",
        "verbal communication", "teamwork", "collaboration", "analytical skills", "analytical thinking",
        "attention to detail", "critical thinking", "time management", "ownership", "leadership", "adaptability",
        "curiosity", "self-starter", "stakeholder management", "interpersonal skills", "scalable systems",
        "best practices", "clean code", "software development", "software engineering", "engineering",
        "programming", "coding", "technology", "computer science",
        "mathematics", "maths", "math", "physics", "statistics degree", "stem", "degree", "bachelor's degree",
        "master's degree", "engineering degree", "computer science degree",
    }
)  # fmt: skip


def not_a_skill(term: str) -> bool:
    """Whether a post's term is culture or a field of study rather than a skill (`NOT_A_SKILL`),
    under any typography and any qualifier tail ("Communication skills")."""
    return normalise_term(term) in NOT_A_SKILL or group_key(term) in NOT_A_SKILL


def group_key(term: str) -> str:
    """One key per requirement, for counting the same skill once however posts spell it (UPSK-01):
    the group's first spelling for a term in a synonym group, else the term with one qualifier tail
    peeled ("Kafka experience" → "kafka") — the same reach as `aliases_for`."""
    chain = _reductions(normalise_term(term))
    for form in chain:
        if group := _GROUPS.get(form):
            return group[0]
    return chain[1] if len(chain) > 1 else chain[0]


def aliases_for(term: str) -> list[str]:
    """Every spelling that counts as `term`.

    The term as written, then with its qualifier tails peeled off one at a time, stopping at the
    first form that names a known group. A term in no group at all still matches its own first
    reduction, so "Kafka experience" finds "Kafka" with nothing mapped — but only one level, since
    peeling further is guessing rather than normalising.
    """
    chain = _reductions(normalise_term(term))
    for form in chain:
        if group := _GROUPS.get(form):
            return list(group)
    return list(dict.fromkeys(chain[:2]))


@lru_cache(maxsize=4096)
def _alias_re(alias: str) -> re.Pattern[str]:
    """The alias as a whole word: no word character touching either end (20 Nov, after
    resume-matcher's `keyword_in_text`, Apache-2.0 — see NOTICE).

    Lookarounds rather than the boundary characters this used to consume, for two reasons. A
    match is now the alias and nothing else, so its span is where the term is on the page — what
    the side-by-side view highlights (`nodes.ats.term_spans`) — and two occurrences one space
    apart are both found. And `\\w` is Unicode, so "café" no longer ends a word at the "é".

    The boundary only applies on a side where the alias itself ends in a word character. "C++"
    and "C#" end in punctuation, so "C++17" and "C#," are C++ and C#; ".NET" starts with it, so
    "ASP.NET" is .NET. The other side still holds: "java" never matches "javascript", nor "c#"
    "abc#".
    """
    head = r"(?<!\w)" if re.match(r"\w", alias) else ""
    tail = r"(?!\w)" if re.search(r"\w$", alias) else ""
    return re.compile(rf"{head}{re.escape(alias)}{tail}")


@lru_cache(maxsize=256)
def _proper_noun_re(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(^|[^A-Za-z0-9]){re.escape(CASE_SENSITIVE_ALIASES[alias])}($|[^A-Za-z0-9])")


def text_has_term(text: str, term: str) -> bool:
    """Whole-word match of a term or any of its synonyms, case-insensitive except where an alias
    is also an ordinary word (`CASE_SENSITIVE_ALIASES`, `AMBIGUOUS_ALIASES`)."""
    text = normalise_text(text)
    haystack = _one_spelling(text.lower())
    own = set(_reductions(normalise_term(term))[:2])
    for alias in aliases_for(term):
        if alias in AMBIGUOUS_ALIASES and alias not in own:
            continue
        if alias in CASE_SENSITIVE_ALIASES:
            if _proper_noun_re(alias).search(text):
                return True
        elif _alias_re(alias).search(haystack):
            return True
    return False


class _Term(Protocol):
    term: str


def in_post[K: _Term](keywords: list[K], post: str | None) -> list[K]:
    """The keywords the post actually contains, under any spelling that counts as them.

    The ATS coverage score divides by this list, so a term the post never asked for is reported
    to the applicant as a gap they must fill — and nothing checked. On the demo fixtures, written
    by hand, 14 of 129 terms were not in their own post: "REST APIs" marked *required* for a post
    that only says "third-party APIs". A model extracting keywords makes the same mistake for
    real, adding what posts like this one usually ask for.

    Applied where keywords are made (`KeywordList`) and where they are read back
    (`fit.cached_evidence`, `Store`), so a score cached before this rule existed is corrected
    without being paid for again. No post means nothing to check against, and nothing to cover.
    """
    if not post:
        return []
    return [k for k in keywords if text_has_term(post, k.term)]


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
    """Every bullet in the CV, keyed by id, plus the summary and each education entry.

    Education entries are lines too since 20 Nov — "BSc (Hons) Economics and Finance, London
    School of Economics and Political Science (LSE)", under the entry's own id. The letter's
    opening is told to say who the applicant is "as their headline, education or summary puts
    it (cite that line)", and until then only the summary had an id to cite: an opening built on
    the degree cited nothing, failed the guard for "no master-CV source" without a repair, and was
    replaced by a 25–40 word plain introduction — one of the reasons two of 20 Nov's seven letters
    came out at 130–133 words. The entry's text is its degree and institution as the CV writes
    them, so a letter citing it is held to that wording like any other line.
    """
    out: dict[str, dict[str, str]] = {}
    if cv.summary:
        out[cv.summary.id] = {"text": cv.summary.text, "where": "Summary"}
    for role in [*cv.experience, *cv.projects]:
        for b in role.bullets:
            out[b.id] = {"text": b.text, "where": f"{role.title} — {role.org}"}
    for ed in cv.education:
        out[ed.id] = {"text": f"{ed.degree}, {ed.institution}", "where": "Education"}
        for b in ed.notes or []:
            out[b.id] = {"text": b.text, "where": ed.degree}
    return out
