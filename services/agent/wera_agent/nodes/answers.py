"""Answer pack (ANS-01…04).

`domain.questions` decides what may be asked of a model. Sensitive questions are never drafted
and link questions are copied from the CV in code, so only genuine written answers reach the
writing tier — and each of those must cite master-CV lines that pass the guard. With nothing to
cite, the answer stays null and the review screen says "your answer needed" (ANS-02/03).
"""

from sqlalchemy.orm import Session

from ..domain.guard import check_cited
from ..domain.questions import kind, links_answer
from ..llm.demo import PackInput
from ..llm.gateway import structured
from ..llm.prompts import cv_text, untrusted
from ..schemas.llm_outputs import AnswersOutput
from ..schemas.pack import AnswerItem, Cited
from .tailor import job_block


def question_id(index: int) -> str:
    return f"q{index + 1}"


def answers(session: Session, inp: PackInput) -> list[AnswerItem]:
    questions = list(inp.questions)
    if not questions:
        return []

    kinds = {question_id(i): kind(q) for i, q in enumerate(questions)}
    draftable = [(question_id(i), q) for i, q in enumerate(questions) if kinds[question_id(i)] == "draft"]

    drafted: dict[str, tuple[str | None, list[str]]] = {}
    if draftable:
        listed = "\n".join(f"[{qid}] {text}" for qid, text in draftable)
        reply = structured(
            session,
            "answers.draft",
            AnswersOutput,
            {
                "cv": cv_text(inp.cv),
                "job": job_block(inp),
                # The questions come from the employer's ATS, so they are data too.
                "questions": untrusted("application questions", listed),
            },
            context={"cv": inp.cv},
            demo_input=inp,
        )
        drafted = {a.id: (a.answer, a.source_ids) for a in reply.answers}

    items: list[AnswerItem] = []
    for index, text in enumerate(questions):
        qid = question_id(index)
        if kinds[qid] == "sensitive":
            items.append(AnswerItem(id=qid, question=text, answer=None, source_ids=[], sensitive=True))
            continue
        if kinds[qid] == "links":
            items.append(AnswerItem(id=qid, question=text, answer=links_answer(inp.cv), source_ids=[], sensitive=False))
            continue
        answer, source_ids = drafted.get(qid, (None, []))
        if answer and source_ids:
            # An answer stands only if its citations hold (§8.5 steps 2–5).
            guard = check_cited(
                [(qid, Cited(text=answer, source_ids=source_ids))], inp.cv, [k.term for k in inp.evidence.keywords]
            )
            if not guard.pass_:
                answer, source_ids = None, []
        else:
            answer, source_ids = None, []
        items.append(AnswerItem(id=qid, question=text, answer=answer, source_ids=source_ids, sensitive=False))
    return items
