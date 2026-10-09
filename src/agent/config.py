"""
Env-based config for the agent loop: ANTHROPIC_API_KEY, MODEL_NAME,
LLM_PROVIDER.

No python-dotenv dependency -- .env (if present) is parsed manually, as a
fallback under real environment variables. The key is never logged or
returned in any error message; only which variable names are missing.

LLM_PROVIDER controls whether the app ever calls the Anthropic API:
- "auto" (default): use Anthropic if ANTHROPIC_API_KEY and MODEL_NAME are
  both set, otherwise fall back to the no-LLM rules interpreter. Never
  raises for a missing key.
- "rules": never use the model; no key required.
- "anthropic": the model is required; a missing key is a ConfigError, same
  as the old unconditional behavior.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOTENV_PATH = PROJECT_ROOT / ".env"

REQUIRED_VARS = ("ANTHROPIC_API_KEY", "MODEL_NAME")
VALID_PROVIDERS = ("auto", "rules", "anthropic")


class ConfigError(Exception):
    pass


def _parse_dotenv(path):
    if not path.exists():
        return {}
    pairs = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            pairs[key] = value
    return pairs


def load_config(dotenv_path=None, env=None):
    """
    Resolves ANTHROPIC_API_KEY, MODEL_NAME, and LLM_PROVIDER from `env`
    (defaults to os.environ), falling back to a .env file (defaults to the
    project root's .env) for any variable `env` doesn't set.

    Returns {"api_key", "model_name", "llm_provider", "has_api_key"}, where
    "llm_provider" is the resolved mode actually in effect:
    - requested "rules" -> "rules"
    - requested "anthropic" -> "anthropic" (raises ConfigError if the key
      or model name is missing)
    - requested "auto" (or unset) -> "anthropic" if both the key and model
      name are present, else "rules"

    Only raises ConfigError when LLM_PROVIDER is explicitly "anthropic" and
    a required variable is missing; "auto" and "rules" never raise for a
    missing key.
    """
    import os

    env = os.environ if env is None else env
    path = DEFAULT_DOTENV_PATH if dotenv_path is None else Path(dotenv_path)
    dotenv_values = _parse_dotenv(path)

    resolved = {}
    for name in REQUIRED_VARS:
        resolved[name] = env.get(name) or dotenv_values.get(name)

    requested_provider = (env.get("LLM_PROVIDER") or dotenv_values.get("LLM_PROVIDER") or "auto").strip().lower()
    if requested_provider not in VALID_PROVIDERS:
        raise ConfigError(
            f"Invalid LLM_PROVIDER '{requested_provider}'. Must be one of: {', '.join(VALID_PROVIDERS)}."
        )

    has_api_key = bool(resolved["ANTHROPIC_API_KEY"])
    has_model_name = bool(resolved["MODEL_NAME"])

    if requested_provider == "rules":
        llm_provider = "rules"
    elif requested_provider == "anthropic":
        missing = [name for name in REQUIRED_VARS if not resolved[name]]
        if missing:
            raise ConfigError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Set them in your shell or in a .env file at the project root."
            )
        llm_provider = "anthropic"
    else:  # auto
        llm_provider = "anthropic" if (has_api_key and has_model_name) else "rules"

    return {
        "api_key": resolved["ANTHROPIC_API_KEY"],
        "model_name": resolved["MODEL_NAME"],
        "llm_provider": llm_provider,
        "has_api_key": has_api_key,
    }
