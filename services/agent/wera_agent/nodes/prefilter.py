"""Which jobs are worth paying a model to score (MATCH-05).

The hard filters remove jobs the person said they don't want. This removes jobs they'd probably
say no to anyway: everything is compared to the CV by embedding similarity, and only the top
few go on to fit scoring. Scoring is the expensive step, so this is where a day's intake stops
being a day's bill.

Embeddings are computed locally, so this costs nothing but time. Demo mode uses a deterministic
hash embedder, which means the demo never downloads a model and its ordering never changes
between runs (DEMO-02).
"""

import hashlib
import re
from typing import Any, Protocol

import logfire
import numpy as np

from ..schemas.cv import MasterCV
from ..schemas.jobs import Job

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIMENSIONS = 384  # what the hash embedder produces, matching the real model's width
DEFAULT_TOP_N = 40  # MATCH-05


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashEmbedder:
    """A stand-in with no model behind it: a bag-of-words vector hashed into fixed dimensions.

    It is not semantic, but it is deterministic, instant and offline — which is what the demo
    and the tests need. Real similarity needs the real embedder.
    """

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r"[a-z0-9+#.]+", text.lower()):
                digest = hashlib.blake2b(word.encode(), digest_size=4).digest()
                out[row, int.from_bytes(digest, "big") % DIMENSIONS] += 1.0
        return out


class FastEmbedder:
    """`bge-small-en-v1.5` through fastembed. The model is downloaded on first use and cached by
    fastembed itself, so only a real (non-demo) run ever pays for it.

    That first use is a ~130 MB download inside a worker thread with nothing watching it, which
    is why it is announced: before this, the first real run simply stopped for however long the
    download took, at the one moment a person is most likely to think WERA has hung. The log line
    is cheap insurance — it costs one line on the first run of an install's life and nothing after.
    """

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        logfire.info("Loading the local embedding model ({model}); the first run downloads it", model=MODEL_NAME)
        self._model = TextEmbedding(model_name=MODEL_NAME)
        logfire.info("Embedding model ready")

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.array(list(self._model.embed(texts)), dtype=np.float32)


_cached: dict[str, Any] = {}


def embedder(demo_mode: bool) -> Embedder:
    """One embedder per process: loading the real model twice is pure waste."""
    key = "hash" if demo_mode else "fast"
    if key not in _cached:
        _cached[key] = HashEmbedder() if demo_mode else FastEmbedder()
    return _cached[key]


def cv_profile_text(cv: MasterCV) -> str:
    """What the person is, in one blob, for comparison against a job ad."""
    parts = [cv.person.headline or "", cv.summary.text if cv.summary else ""]
    parts += [f"{r.title} {r.org} " + " ".join(b.text for b in r.bullets) for r in [*cv.experience, *cv.projects]]
    parts += [s.name for s in cv.skills]
    return "\n".join(p for p in parts if p)


def job_text(job: Job) -> str:
    """What the job is. The description is truncated: the first part of an ad is the job, the
    rest is usually benefits and boilerplate."""
    return "\n".join(
        [job.title, job.company, job.location.raw, (job.description_md or "")[:2000]],
    )


def cosine(reference: np.ndarray, candidates: np.ndarray) -> np.ndarray:
    ref = reference / (np.linalg.norm(reference) or 1.0)
    norms = np.linalg.norm(candidates, axis=1)
    norms[norms == 0] = 1.0
    return (candidates / norms[:, None]) @ ref


# How much a lead whose title names one of the person's target titles is moved up, on top of its
# similarity to the CV (which runs about 0.3–0.9 with the real embedder).
TITLE_BOOST = 0.1


def lead_ranker(cv: MasterCV, titles: list[str], *, demo_mode: bool) -> Any:
    """How the search pool scores a posting before anything is paid to read it (17 Nov): its title
    and snippet against the CV, by the same local embedding as `shortlist`, plus `TITLE_BOOST`
    when the title names a target title. Free, and the same scale wherever the posting was found,
    so with several places the best match is read first.

    None when the embedding model can't be loaded (offline, first run with no network): the search
    then reads in the order it found things, as it did before, rather than not at all."""
    try:
        model = embedder(demo_mode)
        reference = model.embed([cv_profile_text(cv)])[0]
    except Exception:  # noqa: BLE001 — ranking is an improvement, never a reason a search fails
        logfire.warn("Couldn't load the embedding model; the search pool is read in the order found")
        return None
    wanted = [t.casefold() for t in titles if t.strip()]

    def rank(items: list[tuple[str, str]]) -> list[float]:
        if not items:
            return []
        scores = cosine(reference, model.embed([f"{title}. {snippet}" for title, snippet in items]))
        return [
            float(score) + (TITLE_BOOST if any(w in title.casefold() for w in wanted) else 0.0)
            for (title, _snippet), score in zip(items, scores, strict=True)
        ]

    return rank


# MATCH-01 by meaning (20 Nov): a title at least this close to one of the person's target titles,
# by the same local embedding, is one of their titles even when it doesn't carry every word of one.
# Set on the finance profile's 374 word-rule drops (841 API leads, 20 Nov), labelled by hand: of the
# titles at 0.85 or more about 4 in 5 are the work ("Finance Analyst" 0.96, "Commercial Finance
# Analyst" 0.92, "Corporate Credit Research Analyst" 0.86), from 0.80 to 0.85 about 2 in 3 ("Equity
# Analyst" 0.82, "Investment Analyst - Private Equity" 0.81), and from 0.78 to 0.80 one in two
# ("Procurement Analyst", "Fraud Analyst", "Business Systems Analyst" come in). 0.80 keeps 85 of
# the 374. What it still drops but the CV is close to is a near miss for Upskills (UPSK-06).
TITLE_MEANING_MIN = 0.80
# Title vectors, kept for the process: the same few hundred titles come back search after search,
# and a watchlist's thousands are checked on every refilter.
_titles: dict[str, np.ndarray] = {}


def warm_titles(titles: list[str]) -> None:
    """Embed every title not yet seen, in one batch: one call for a search's leads or a refilter's
    jobs, rather than one per title as `title_closeness` would make them."""
    model = _title_model()
    fresh = list(dict.fromkeys(t.strip() for t in titles if t.strip() and t.strip() not in _titles))
    if model is None or not fresh:
        return
    for title, vector in zip(fresh, model.embed(fresh), strict=True):
        _titles[title] = vector


def title_closeness(title: str, targets: list[str]) -> float | None:
    """How close `title` is to the nearest target title, 0–1, by meaning; None when it can't be told.

    Only the real embedder judges meaning: in demo mode (and the tests) the hash embedder counts
    shared words, which the word rule already does better, so this says nothing there and the word
    rule alone decides, as before 20 Nov. Never raises: a model that won't load leaves the word rule.
    """
    wanted = [t.strip() for t in targets if t.strip()]
    if not title.strip() or not wanted or _title_model() is None:
        return None
    try:
        warm_titles([title, *wanted])
        mine = _titles[title.strip()]
        return float(cosine(mine, np.stack([_titles[t] for t in wanted])).max())
    except Exception:  # noqa: BLE001 — the word rule still stands
        return None


def _title_model() -> Embedder | None:
    from ..settings import get_settings

    if get_settings().wera_demo_mode:
        return None
    try:
        return embedder(False)
    except Exception:  # noqa: BLE001
        return None


def shortlist(jobs: list[Job], cv: MasterCV, *, demo_mode: bool, top_n: int = DEFAULT_TOP_N) -> list[Job]:
    """The `top_n` jobs closest to the CV, best first. Fewer jobs than that go through as they are.

    Ordering here is a cheap guess, not a verdict: the fit score decides, and this only chooses
    what gets scored.
    """
    if len(jobs) <= top_n:
        return jobs
    model = embedder(demo_mode)
    vectors = model.embed([cv_profile_text(cv), *[job_text(j) for j in jobs]])
    scores = cosine(vectors[0], vectors[1:])
    order = np.argsort(-scores)[:top_n]
    logfire.info(
        "Pre-filter kept {kept} of {total} jobs for scoring",
        kept=len(order),
        total=len(jobs),
    )
    return [jobs[i] for i in order]
