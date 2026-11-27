"""agent-squad: one interface over several AI workers, with a master in charge.

    import agent_squad as squad
    r = squad.ask("gemini", "List 5 festival ideas as JSON", json_mode=True)
    print(r.text, r.worker, r.account)

A spec is "<worker>" or "<worker>:<model>", e.g. "local:qwen3:8b", "gemini:gemini-3-pro-preview",
"claude:opus". If the worker fails, the config's fallback list is tried in order.
"""
from __future__ import annotations

from pathlib import Path

from . import usage
from .config import load_config
from .workers import WORKERS, AuthError, QuotaError, Result, Worker, WorkerError

__version__ = "0.1.0"
__all__ = ["ask", "get_worker", "parse_spec", "status", "Result", "WorkerError", "QuotaError", "AuthError",
           "WORKERS", "load_config"]


def parse_spec(spec: str) -> tuple[str, str]:
    kind, _, model = (spec or "").partition(":")
    return kind.strip(), model.strip()


def state_dir(cfg: dict) -> Path:
    import os
    return Path(os.path.expanduser(cfg.get("state_dir") or "~/.agent-squad/state"))


def get_worker(name: str, cfg: dict | None = None) -> Worker:
    cfg = cfg or load_config()
    if name not in WORKERS:
        raise WorkerError(f"unknown worker {name!r}; known: {', '.join(WORKERS)}")
    return WORKERS[name]((cfg.get("workers") or {}).get(name) or {}, state_dir(cfg))


def ask(spec: str | None, prompt: str, *, task: str = "ask", system: str = "", json_mode: bool = False,
        temperature: float | None = None, fallback: list[str] | None = None, config: dict | None = None,
        usage_file: str | Path | None = None, log=print, **extra) -> Result:
    """Send a prompt to a worker; on failure try each fallback spec in turn."""
    cfg = config if config is not None else load_config()
    spec = spec or cfg.get("default_worker", "local")
    chain = [spec] + [s for s in (cfg.get("fallback", []) if fallback is None else fallback)
                      if parse_spec(s)[0] != parse_spec(spec)[0]]
    ledger = Path(usage_file) if usage_file else state_dir(cfg) / "usage.json"
    errors = []
    for i, s in enumerate(chain):
        kind, model = parse_spec(s)
        try:
            w = get_worker(kind, cfg)
        except WorkerError as e:
            errors.append(str(e))
            continue
        if log:
            log(f"  [{w.role}] {kind}{' ' + model if model else ''} -> {task}" + (" (fallback)" if i else ""))
        kw = {"web": extra["web"]} if kind == "claude" and "web" in extra else {}
        try:
            r = w.generate(prompt, system=system, model=model or None, json_mode=json_mode,
                           temperature=temperature, task=task, **kw)
        except WorkerError as e:
            usage.record(ledger, task, worker=kind, ok=False)
            errors.append(f"{kind}: {e}")
            if log:
                log(f"  {kind} failed: {str(e)[:200]}")
            continue
        usage.record(ledger, task, r)
        return r
    raise WorkerError("no worker could answer. " + " | ".join(errors))


def status(cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    out = {}
    for name in WORKERS:
        try:
            out[name] = get_worker(name, cfg).status()
        except Exception as e:  # noqa: BLE001
            out[name] = {"name": name, "up": False, "error": str(e)}
    return out
