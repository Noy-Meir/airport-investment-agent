import pytest

from src.agent.config import ConfigError, load_config


def test_auto_with_key_and_model_resolves_anthropic(tmp_path):
    empty_dotenv = tmp_path / ".env"
    config = load_config(
        dotenv_path=empty_dotenv,
        env={"ANTHROPIC_API_KEY": "sk-ant-test", "MODEL_NAME": "claude-test-model"},
    )
    assert config["llm_provider"] == "anthropic"
    assert config["has_api_key"] is True


def test_auto_without_key_resolves_rules(tmp_path):
    empty_dotenv = tmp_path / ".env"
    config = load_config(dotenv_path=empty_dotenv, env={})
    assert config["llm_provider"] == "rules"
    assert config["has_api_key"] is False


def test_auto_without_model_name_resolves_rules(tmp_path):
    empty_dotenv = tmp_path / ".env"
    config = load_config(dotenv_path=empty_dotenv, env={"ANTHROPIC_API_KEY": "sk-ant-test"})
    assert config["llm_provider"] == "rules"


def test_explicit_rules_provider_never_raises_without_key(tmp_path):
    empty_dotenv = tmp_path / ".env"
    config = load_config(dotenv_path=empty_dotenv, env={"LLM_PROVIDER": "rules"})
    assert config["llm_provider"] == "rules"
    assert config["has_api_key"] is False


def test_explicit_anthropic_provider_raises_without_key(tmp_path):
    empty_dotenv = tmp_path / ".env"
    with pytest.raises(ConfigError):
        load_config(dotenv_path=empty_dotenv, env={"LLM_PROVIDER": "anthropic"})


def test_explicit_anthropic_provider_resolves_with_key(tmp_path):
    empty_dotenv = tmp_path / ".env"
    config = load_config(
        dotenv_path=empty_dotenv,
        env={"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-test", "MODEL_NAME": "claude-test-model"},
    )
    assert config["llm_provider"] == "anthropic"


def test_invalid_provider_raises(tmp_path):
    empty_dotenv = tmp_path / ".env"
    with pytest.raises(ConfigError):
        load_config(dotenv_path=empty_dotenv, env={"LLM_PROVIDER": "bogus"})
