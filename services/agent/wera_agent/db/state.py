"""The two singleton rows, read straight from a session.

The LLM gateway needs the saved settings (model per tier, budget, redaction) and the contact
details (to redact them), and `services.store` needs the gateway. Keeping these readers here
means neither has to import the other.
"""

from typing import Any

from sqlalchemy.orm import Session

from ..errors import ApiError
from ..schemas.cv import Contact
from .models import ContactRow, SettingsRow

NOT_SEEDED = "WERA hasn't set up its database yet. Run `wera reset`."


def settings_data(session: Session) -> dict[str, Any]:
    row = session.get(SettingsRow, 1)
    if row is None:
        raise ApiError(503, "NOT_SEEDED", NOT_SEEDED)
    return row.data


def save_settings_data(session: Session, data: dict[str, Any]) -> None:
    row = session.get(SettingsRow, 1)
    if row is None:
        raise ApiError(503, "NOT_SEEDED", NOT_SEEDED)
    row.data = data


def contact(session: Session) -> Contact:
    """CV-06: stored apart from the CV. Read here only to redact it (PRIV-01) or export it."""
    row = session.get(ContactRow, 1)
    if row is None:
        raise ApiError(503, "NOT_SEEDED", NOT_SEEDED)
    return Contact(email=row.email, phone=row.phone, address=row.address)
