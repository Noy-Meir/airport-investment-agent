"""
Env-based config for the agent loop: ANTHROPIC_API_KEY, MODEL_NAME.

No python-dotenv dependency -- .env (if present) is parsed manually, as a
fallback under real environment variables. The key is never logged or
returned in any error message; only which variable names are missing.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOTENV_PATH = PROJECT_ROOT / ".env"

REQUIRED_VARS = ("ANTHROPIC_API_KEY", "MODEL_NAME")


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
    Resolves ANTHROPIC_API_KEY and MODEL_NAME from `env` (defaults to
    os.environ), falling back to a .env file (defaults to the project
    root's .env) for any variable `env` doesn't set. Raises ConfigError
    naming the missing variable(s) -- never their values -- if either is
    still unset.
    """
    import os

    env = os.environ if env is None else env
    path = DEFAULT_DOTENV_PATH if dotenv_path is None else Path(dotenv_path)
    dotenv_values = _parse_dotenv(path)

    resolved = {}
    for name in REQUIRED_VARS:
        resolved[name] = env.get(name) or dotenv_values.get(name)

    missing = [name for name in REQUIRED_VARS if not resolved[name]]
    if missing:
        raise ConfigError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Set them in your shell or in a .env file at the project root."
        )

    return {"api_key": resolved["ANTHROPIC_API_KEY"], "model_name": resolved["MODEL_NAME"]}
