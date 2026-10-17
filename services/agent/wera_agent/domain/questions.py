"""Which application questions a model is allowed to answer (ANS-02…04).

Two kinds never reach a model, and both are decided here so the rule is the same whoever asks:

* **Sensitive** — right to work, visas, salary, and demographic or personal questions. WERA
  does not speak for the applicant on these; the answer is left for them (ANS-04).
* **Links** — "what's your GitHub?". The answer is a field of the master CV, copied across in
  code. It isn't a written claim, so there is nothing for the citation guard to check, and
  nothing a model could add.

Everything else is drafted from cited CV lines and has to pass the guard.
"""

import re

from ..schemas.cv import MasterCV

SENSITIVE = re.compile(
    r"(authori[sz]ed to work|right to work|visa|sponsor|salary|compensation|gender|ethnic|\brace\b|disabilit|veteran|"
    r"criminal|convict|pronoun|sexual orientation|demographic|\bage\b)",
    re.I,
)

LINKS = re.compile(r"(github|portfolio|personal website|your website|links?\b)", re.I)


def kind(question: str) -> str:
    """`"sensitive"`, `"links"` or `"draft"`."""
    if SENSITIVE.search(question):
        return "sensitive"
    return "links" if LINKS.search(question) else "draft"


def links_answer(cv: MasterCV) -> str | None:
    return "\n".join(f"{link.label}: {link.url}" for link in cv.person.links) or None
