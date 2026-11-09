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


# Tiers whose model the person chose themselves on the Settings screen. Written by
# `Store.put_settings`; everything not in here follows `config/wera.yaml`.
MODELS_CHOSEN = "modelsChosenByUser"


def settings_data(session: Session) -> dict[str, Any]:
    row = session.get(SettingsRow, 1)
    if row is None:
        raise ApiError(503, "NOT_SEEDED", NOT_SEEDED)
    return with_model_defaults(row.data)


def with_model_defaults(data: dict[str, Any]) -> dict[str, Any]:
    """Resolve each model tier: the person's own choice if they made one, else `config/wera.yaml`.

    **This is the half that was wrong, and it cost real money.** The first version of this only
    filled in tiers the stored blob was *missing*, which fixed the crash that adding a tier would
    otherwise have caused but left an uglier bug behind: the stored value won unconditionally for
    every tier that already existed. Settings are one JSON blob written at seed time (SET-01), so
    a tier's model was frozen at whatever the config said on the day WERA was first run, and
    editing `config/wera.yaml` afterwards did nothing at all.

    What that looked like in practice: the writing tier was moved off DeepSeek v4.1 Flash — which
    this project's own eval measured at 63.6% usable against 97.6% — and every existing install
    went on using DeepSeek anyway. `letter.write` kept failing about half its calls and nobody
    could see why, because the config file plainly said otherwise.

    So the rule is now "a choice beats a default, and a default is allowed to change". A tier the
    person has never touched tracks the config file, which is what a file of defaults is for. A
    tier they picked in Settings is recorded in `MODELS_CHOSEN` and is never overridden.

    Note for an install that predates `MODELS_CHOSEN`: it has no record of what was chosen, so
    every tier reverts to the config default once. That is the right way round — the stored values
    there came from the seed rather than from a person — but someone who had deliberately picked a
    model before this change will need to pick it again.
    """
    from ..config import wera

    defaults = wera().models.model_dump()
    stored = data.get("models") or {}
    chosen = set(data.get(MODELS_CHOSEN) or [])
    resolved = {tier: (stored.get(tier) or model) if tier in chosen else model for tier, model in defaults.items()}
    return data if resolved == stored else {**data, "models": resolved}


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
