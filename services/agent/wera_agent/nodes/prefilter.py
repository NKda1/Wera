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
    fastembed itself, so only a real (non-demo) run ever pays for it."""

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=MODEL_NAME)

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
