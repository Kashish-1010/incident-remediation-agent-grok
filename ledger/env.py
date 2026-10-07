"""Load a local .env file without overriding variables already set in the process."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path | None = None) -> Path | None:
    env_path = path if path is not None else ROOT / ".env"
    if not env_path.is_file():
        return None
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value
    return env_path


def api_key() -> str:
    load_dotenv()
    return os.environ.get("XAI_API_KEY", "").strip()


def key_status() -> str:
    load_dotenv()
    value = os.environ.get("XAI_API_KEY", "").strip()
    if not value:
        return "XAI_API_KEY is not set. Copy .env.example to .env and set the variable there, or export it in the shell."
    prefix = value[:4]
    return f"XAI_API_KEY is loaded ({len(value)} characters, starts with {prefix}). The value is not printed."
