"""SET-01: which model a tier actually uses, and who gets to decide.

This exists because of a bug that cost real money and was invisible. `config/wera.yaml`'s
`models:` block looked like the place to change a tier, and on any install that had already been
seeded it did nothing at all — the value stored at seed time won unconditionally. The writing tier
was moved off a model this project's own eval measured at 63.6% usable, and every existing install
carried on using it, failing about half of its `letter.write` calls.
"""

from wera_agent.config import wera
from wera_agent.db import state
from wera_agent.db.session import transaction
from wera_agent.schemas.settings import SettingsPatch
from wera_agent.services.store import Store


def stored(session) -> dict:
    from wera_agent.db.models import SettingsRow

    return session.get(SettingsRow, 1).data


class TestADefaultTracksTheConfigFile:
    def test_a_tier_nobody_chose_follows_config(self, client):
        """A file of defaults whose defaults can't change is documentation, not configuration."""
        with transaction() as session:
            raw = stored(session)
            raw["models"] = {**raw["models"], "writing": "some/stale-model-from-an-old-seed"}
            resolved = state.with_model_defaults(raw)
        assert resolved["models"]["writing"] == wera().models.writing

    def test_a_tier_the_config_has_gained_is_filled_in(self, client):
        """The original reason this function exists: `gateway` reads `models[tier]` on every call,
        so a tier added to config after an install was seeded would have been a `KeyError` on
        every model call and a 500 on the Settings screen."""
        with transaction() as session:
            raw = stored(session)
            raw["models"] = {"fast": wera().models.fast}
            resolved = state.with_model_defaults(raw)
        assert set(resolved["models"]) == set(wera().models.model_dump())

    def test_the_real_database_resolves_the_configured_writing_tier(self, client):
        """The regression test for the actual incident: whatever is stored, a freshly seeded
        install reports the model the config file names."""
        with transaction() as session:
            assert state.settings_data(session)["models"]["writing"] == wera().models.writing


class TestAChoiceBeatsADefault:
    def test_a_tier_picked_in_settings_is_not_overridden(self, client):
        with transaction() as session:
            store = Store(session)
            models = {**store.get_settings().models.model_dump(by_alias=True), "writing": "qwen/qwen3.8-max-0902"}
            store.put_settings(SettingsPatch.model_validate({"models": models}))
        with transaction() as session:
            assert state.settings_data(session)["models"]["writing"] == "qwen/qwen3.8-max-0902"

    def test_choosing_one_tier_does_not_freeze_the_others(self, client):
        """Recorded per tier rather than as one flag: picking a writing model must not silently
        pin `fast`, `select` and `scoring` to whatever they happened to be that day."""
        with transaction() as session:
            store = Store(session)
            models = {**store.get_settings().models.model_dump(by_alias=True), "writing": "qwen/qwen3.8-max-0902"}
            store.put_settings(SettingsPatch.model_validate({"models": models}))
        with transaction() as session:
            raw = stored(session)
            assert raw[state.MODELS_CHOSEN] == ["writing"]
            # The others still track config, so a later config change reaches them.
            raw["models"] = {**raw["models"], "scoring": "some/stale-model"}
            assert state.with_model_defaults(raw)["models"]["scoring"] == wera().models.scoring

    def test_saving_settings_without_touching_models_chooses_nothing(self, client):
        with transaction() as session:
            Store(session).put_settings(SettingsPatch.model_validate({"cvPages": 2}))
        with transaction() as session:
            assert not stored(session).get(state.MODELS_CHOSEN)
