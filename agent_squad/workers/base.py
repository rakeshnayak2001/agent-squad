"""The worker interface every AI backend implements.

A worker turns one prompt into one answer. It never decides what to do next: that is the
master's job (Claude Code, or whatever program calls the harness).

    from agent_squad.workers.base import Worker, Result

    class MyWorker(Worker):
        name = "mine"
        def generate(self, prompt, *, system="", model=None, json_mode=False,
                     temperature=None, task="") -> Result: ...
        def status(self) -> dict: ...

Register it in agent_squad/workers/__init__.py and it can be routed to with "mine" or "mine:<model>".
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Result:
    text: str
    worker: str
    model: str = ""
    account: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    raw: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


class WorkerError(RuntimeError):
    """The worker could not answer (not installed, offline, bad reply). Try the next one."""


class QuotaError(WorkerError):
    """The account hit a quota or rate limit. Another account may still work."""


class AuthError(WorkerError):
    """The account is not signed in (or its sign-in expired)."""


class Worker:
    name = "base"
    #: "master" workers may do judgement work; "worker" ones get repetitive jobs.
    role = "worker"

    def __init__(self, settings: dict | None = None, state_dir=None):
        self.settings = settings or {}
        self.state_dir = state_dir

    def generate(self, prompt: str, *, system: str = "", model: str | None = None, json_mode: bool = False,
                 temperature: float | None = None, task: str = "") -> Result:
        raise NotImplementedError

    def status(self) -> dict:
        return {"name": self.name, "up": False}

    @staticmethod
    def with_system(prompt: str, system: str, json_mode: bool) -> str:
        """For CLIs with no separate system prompt: fold it into the prompt."""
        parts = []
        if system:
            parts.append(f"[Instructions]\n{system.strip()}\n")
        parts.append(prompt)
        if json_mode:
            parts.append("\nReply with valid JSON only: no prose, no code fences.")
        return "\n".join(parts)
