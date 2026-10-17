"""What a call cost, and whether the weekly budget allows another one (COST-01, COST-02).

The price table is in config/wera.yaml. An unlisted model is charged at the deliberately high
`default` rate, so a model nobody priced can't quietly spend the budget.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..clock import iso, now_ms
from ..config import wera
from ..db.models import LlmCallRow

DAY_MS = 24 * 60 * 60 * 1000
WEEK_MS = 7 * DAY_MS
WARN_AT = 0.8  # COST-02


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price = wera().price(model)
    return (input_tokens * price.input + output_tokens * price.output) / 1_000_000


def spent_this_week_usd(session: Session, now: int) -> float:
    """Spend over the last seven days, from the rows the gateway writes."""
    since = iso(now - WEEK_MS)
    total = session.scalar(select(func.sum(LlmCallRow.cost_usd)).where(LlmCallRow.called_at >= since))
    return round(float(total or 0), 6)


class Budget:
    """The answer to "can WERA make another call?", with the numbers behind it."""

    def __init__(self, spent: float, limit: float) -> None:
        self.spent = spent
        self.limit = limit

    @property
    def fraction(self) -> float:
        return 1.0 if self.limit <= 0 else self.spent / self.limit

    @property
    def reached(self) -> bool:
        """COST-01: at 100% the next call is refused."""
        return self.spent >= self.limit

    @property
    def warn(self) -> bool:
        return not self.reached and self.fraction >= WARN_AT


def budget(session: Session, limit_usd: float, now: int | None = None) -> Budget:
    return Budget(spent_this_week_usd(session, now if now is not None else now_ms()), limit_usd)
