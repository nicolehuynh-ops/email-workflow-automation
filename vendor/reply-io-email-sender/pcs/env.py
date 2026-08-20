import os
from pathlib import Path


def load_env():
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        raise RuntimeError("Missing .env file. Copy .env.example to .env and add REPLY_IO_API_KEY.")

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        os.environ.setdefault(key, value)

    if not os.getenv("REPLY_IO_API_KEY"):
        raise RuntimeError("REPLY_IO_API_KEY is missing from .env.")
