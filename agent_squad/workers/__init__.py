"""Worker registry. To add a backend (e.g. Codex): write workers/codex.py with a Worker subclass
and add one line to WORKERS below. Then route tasks to "codex" or "codex:<model>"."""
from .base import AuthError, QuotaError, Result, Worker, WorkerError
from .claude_code import ClaudeCodeWorker
from .gemini_cli import GeminiCliWorker
from .ollama import OllamaWorker

WORKERS: dict[str, type[Worker]] = {
    "claude": ClaudeCodeWorker,
    "local": OllamaWorker,
    "gemini": GeminiCliWorker,
    # "codex": CodexWorker,   # later: `codex exec --json`, same interface
}

__all__ = ["WORKERS", "Worker", "Result", "WorkerError", "QuotaError", "AuthError"]
