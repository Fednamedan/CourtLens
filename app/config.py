"""Загрузка config.yaml с подстановкой ${ENV_VAR}."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
_ENV = re.compile(r"\$\{(\w+)\}")


def _subst(v: Any) -> Any:
    if isinstance(v, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), ""), v)
    if isinstance(v, dict):
        return {k: _subst(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_subst(x) for x in v]
    return v


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    with open(cfg_path, encoding="utf-8") as fh:
        return _subst(yaml.safe_load(fh))
