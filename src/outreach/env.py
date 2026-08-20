"""Minimal local .env reader; environment variables always take precedence."""

import os
from pathlib import Path
from typing import Dict


def load_dotenv(path: Path) -> Dict[str, str]:
    values = dict(os.environ)
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in values:
            values[key] = value.strip().strip('"').strip("'")
    return values
