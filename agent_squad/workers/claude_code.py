"""Claude Code (`claude -p`) on the user's Claude subscription. The default master."""
from __future__ import annotations

import json
import shutil
import subprocess
import time

from .base import AuthError, Result, Worker, WorkerError


class ClaudeCodeWorker(Worker):
    name = "claude"
    role = "master"

    def exe(self) -> str | None:
        return shutil.which(self.settings.get("command") or "claude")

    def generate(self, prompt, *, system="", model=None, json_mode=False, temperature=None, task="",
                 web: bool | None = None) -> Result:
        exe = self.exe()
        if not exe:
            raise WorkerError("'claude' not found on PATH. Install Claude Code and run `claude` once to log in.")
        cmd = [exe, "-p", "--output-format", "json"]
        allow_web = self.settings.get("allow_web_search", True) if web is None else web
        if allow_web:
            cmd += ["--allowedTools", "WebSearch,WebFetch"]
        model = model if model is not None else self.settings.get("model", "")
        if model:
            cmd += ["--model", model]
        if system:
            cmd += ["--append-system-prompt", system]
        if json_mode:
            prompt += "\n\nReply with valid JSON only."
        t0 = time.time()
        res = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                             timeout=float(self.settings.get("timeout", 900)))
        secs = time.time() - t0
        if res.returncode != 0:
            raise WorkerError(f"claude exited {res.returncode}: {(res.stderr or res.stdout)[-800:]}")
        try:
            outer = json.loads(res.stdout)
        except json.JSONDecodeError:
            return Result(res.stdout, self.name, model or "", seconds=secs)
        if outer.get("is_error"):
            msg = str(outer.get("result"))
            if "login" in msg.lower() or "auth" in msg.lower():
                raise AuthError(f"Claude Code is not logged in: {msg}. Run `claude` once.")
            raise WorkerError(f"Claude Code returned an error: {msg}")
        u = outer.get("usage") or {}
        return Result(
            text=outer.get("result", ""), worker=self.name, model=model or "default",
            tokens_in=(u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                       + u.get("cache_creation_input_tokens", 0)),
            tokens_out=u.get("output_tokens", 0), cost_usd=outer.get("total_cost_usd", 0) or 0,
            seconds=secs, raw=outer)

    def status(self) -> dict:
        return {"name": self.name, "role": self.role, "up": bool(self.exe()),
                "model": self.settings.get("model", "") or "default"}
