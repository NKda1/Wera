"""SET-02's "Test connection" for the model provider, against recorded OpenRouter replies."""

import httpx
import pytest
import respx
from pydantic import SecretStr

from wera_agent.services.providers import check_llm
from wera_agent.settings import get_settings

KEY = "https://openrouter.ai/api/v1/key"


@pytest.fixture
def env():
    return get_settings().model_copy(
        update={"llm_base_url": "https://openrouter.ai/api/v1", "llm_api_key": SecretStr("sk-or-v1-test")}
    )


@respx.mock
def test_a_key_with_credit_left_passes(env):
    respx.get(KEY).mock(return_value=httpx.Response(200, json={"data": {"limit": 5, "usage": 1.25}}))
    result = check_llm(env)
    assert result.ok
    assert "$3.75 left of $5.00" in result.message


@respx.mock
def test_a_key_that_has_spent_its_own_limit_fails(env):
    """15 Nov: this said "accepted the key. $0.00 left of $5.00" with ok=True while every model
    call was being refused — the key works, but OpenRouter checks its limit before the account's."""
    respx.get(KEY).mock(return_value=httpx.Response(200, json={"data": {"limit": 5, "usage": 5.0008}}))
    result = check_llm(env)
    assert (result.ok, result.code) == (False, "LLM_KEY_LIMIT")
    assert "$0.00 of $5.00" in result.message
    assert "openrouter.ai/settings/keys" in result.message


@respx.mock
def test_a_key_with_no_limit_passes(env):
    respx.get(KEY).mock(return_value=httpx.Response(200, json={"data": {"limit": None, "usage": 40}}))
    result = check_llm(env)
    assert result.ok
    assert "No spending limit" in result.message
