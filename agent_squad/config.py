"""Harness settings: squad.json, found at $SQUAD_CONFIG or ~/.agent-squad/squad.json.

A host program (like the Shorts app) can skip the file and pass its own settings dict instead.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

HOME = Path(os.path.expanduser(os.environ.get("SQUAD_HOME", "~/.agent-squad")))

DEFAULT_CONFIG = {
    "state_dir": str(HOME / "state"),
    "workers": {
        "claude": {"model": "", "allow_web_search": True, "timeout": 900},
        "local": {"url": "http://localhost:11434", "model": "qwen3:8b", "temperature": 0.3, "timeout": 600},
        "gemini": {"backend": "api", "command": "gemini", "model": "gemini-3-pro-preview", "rotation": "failover",
                   "accounts": [{"name": "acc1", "label": "Gemini key 1"},
                                {"name": "acc2", "label": "Gemini key 2"}],
                   "accounts_dir": str(HOME / "gemini")},
    },
    "default_worker": "local",
    # tried in order when the chosen worker fails (never includes the one that just failed)
    "fallback": ["local", "claude"],
}


def config_path() -> Path:
    return Path(os.path.expanduser(os.environ.get("SQUAD_CONFIG", str(HOME / "squad.json"))))


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(override: dict | None = None) -> dict:
    cfg = DEFAULT_CONFIG
    p = config_path()
    if p.exists():
        cfg = _merge(cfg, json.loads(p.read_text(encoding="utf-8")))
    return _merge(cfg, override or {})


def write_default_config() -> Path:
    p = config_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    return p
