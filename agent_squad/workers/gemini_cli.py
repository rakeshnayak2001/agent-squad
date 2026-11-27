"""Gemini through the Gemini CLI, signed in with Google AI Pro accounts (or API keys), with account rotation.

Each account gets its own CLI home folder (env var GEMINI_CLI_HOME by default), so the two Google
sign-ins live side by side. Sign in once per account with `squad gemini login <account>`.

Headless call per prompt:  gemini -p <prompt> --output-format json [-m <model>]
run in an empty temporary folder, so the CLI's file tools cannot touch your projects.

Settings (squad.json -> workers.gemini):
  command        "gemini" (configurable in case your plan moves to another CLI)
  model          "gemini-3-pro-preview"
  rotation       "failover" | "round_robin"
  accounts       [{"name": "acc1", "label": "Pro account 1"}, {"name": "acc2"}]
                 an account with "api_key_env": "MY_KEY_VAR" uses that API key instead of a Google sign-in
  accounts_dir   "~/.agent-squad/gemini"
  home_env       "GEMINI_CLI_HOME"
  extra_args     []            passed to every call
  timeout        600
  rate_limit_cooldown_minutes  30   after a per-minute limit
  quota_reset_utc_hour         8    daily quotas assumed to reset at this UTC hour (about midnight US Pacific)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ..accounts import AccountPool
from .base import AuthError, QuotaError, Result, Worker, WorkerError

DEFAULTS = {
    "command": "gemini", "model": "gemini-3-pro-preview", "rotation": "failover",
    "accounts": [{"name": "acc1", "label": "Pro account 1"}, {"name": "acc2", "label": "Pro account 2"}],
    "accounts_dir": "~/.agent-squad/gemini", "home_env": "GEMINI_CLI_HOME", "extra_args": [],
    "timeout": 600, "rate_limit_cooldown_minutes": 30, "quota_reset_utc_hour": 8,
}
QUOTA_RE = re.compile(r"quota|RESOURCE_EXHAUSTED|\b429\b|rate.?limit|usage limit|too many requests", re.I)
DAILY_RE = re.compile(r"per.?day|daily|today|tomorrow", re.I)
AUTH_RE = re.compile(r"\bauth|log ?in|sign ?in|credential|unauthenticated|\b401\b|oauth", re.I)
MAX_ARG_PROMPT = 6000  # longer prompts go through stdin (Windows command lines max out near 32k)


class GeminiCliWorker(Worker):
    name = "gemini"

    @property
    def cfg(self) -> dict:
        return {**DEFAULTS, **self.settings}

    @property
    def pool(self) -> AccountPool:
        c = self.cfg
        return AccountPool(self.name, c["accounts"], c["accounts_dir"], Path(self.state_dir), c["rotation"])

    def exe(self) -> str | None:
        return shutil.which(self.cfg["command"])

    # ------------------------------------------------------------ per-account environment
    def account_env(self, account: dict) -> dict:
        env = dict(os.environ)
        home = self.pool.home(account)
        home.mkdir(parents=True, exist_ok=True)
        env[self.cfg["home_env"]] = str(home)
        if account.get("api_key_env"):
            key = os.environ.get(account["api_key_env"], "")
            if not key:
                raise AuthError(f"env var {account['api_key_env']} is empty")
            env["GEMINI_API_KEY"] = key
        else:
            env.pop("GEMINI_API_KEY", None)  # a stray key would bypass the Pro sign-in
        return env

    def signed_in(self, account: dict) -> bool:
        if account.get("api_key_env"):
            return bool(os.environ.get(account["api_key_env"]))
        g = self.pool.home(account) / ".gemini"
        return any((g / f).exists() for f in ("oauth_creds.json", "google_accounts.json"))

    # ------------------------------------------------------------ one call
    def _run(self, account: dict, prompt: str, model: str) -> tuple[subprocess.CompletedProcess, float]:
        exe = self.exe()
        if not exe:
            raise WorkerError(f"'{self.cfg['command']}' not found on PATH. Install it: npm install -g @google/gemini-cli")
        use_stdin = len(prompt) > MAX_ARG_PROMPT
        cmd = [exe, "-p", "Answer the request given on standard input." if use_stdin else prompt,
               "--output-format", "json"]
        if model:
            cmd += ["-m", model]
        cmd += list(self.cfg["extra_args"])
        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix="squad-gemini-") as cwd:
            res = subprocess.run(cmd, input=prompt if use_stdin else "", capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", cwd=cwd, env=self.account_env(account),
                                 timeout=float(self.cfg["timeout"]))
        return res, time.time() - t0

    def _cooldown_until(self, msg: str) -> dt.datetime:
        now = dt.datetime.now(dt.timezone.utc)
        if DAILY_RE.search(msg):
            reset = now.replace(hour=int(self.cfg["quota_reset_utc_hour"]), minute=5, second=0, microsecond=0)
            return reset if reset > now else reset + dt.timedelta(days=1)
        return now + dt.timedelta(minutes=float(self.cfg["rate_limit_cooldown_minutes"]))

    @staticmethod
    def _parse(stdout: str) -> dict:
        s = stdout.strip()
        try:
            return json.loads(s)
        except ValueError:
            i = s.find("{")  # the CLI may print a notice line before the JSON
            if i >= 0:
                try:
                    return json.loads(s[i:])
                except ValueError:
                    pass
        return {}

    @staticmethod
    def _tokens(stats: dict) -> tuple[int, int, str]:
        tin = tout = 0
        used = ""
        for name, m in ((stats or {}).get("models") or {}).items():
            t = (m or {}).get("tokens") or {}
            tin += int(t.get("prompt", 0) or 0)
            tout += int(t.get("candidates", 0) or 0) + int(t.get("thoughts", 0) or 0)
            used = used or name
        return tin, tout, used

    def call_account(self, account: dict, prompt: str, model: str) -> Result:
        res, secs = self._run(account, prompt, model)
        out = self._parse(res.stdout)
        err = out.get("error")
        msg = (json.dumps(err) if err else "") + " " + (res.stderr or "")
        if res.returncode != 0 or err or not out:
            text = msg.strip() or res.stdout[-500:] or f"exit code {res.returncode}"
            if QUOTA_RE.search(text):
                raise QuotaError(text[-500:])
            if AUTH_RE.search(text):
                raise AuthError(text[-500:])
            raise WorkerError(f"gemini exited {res.returncode}: {text[-500:]}")
        tin, tout, used = self._tokens(out.get("stats") or {})
        return Result(str(out.get("response", "")).strip(), self.name, used or model, account=account["name"],
                      tokens_in=tin, tokens_out=tout, seconds=secs, raw=out)

    def generate(self, prompt, *, system="", model=None, json_mode=False, temperature=None, task="") -> Result:
        model = model or self.cfg["model"]
        full = self.with_system(prompt, system, json_mode)
        pool = self.pool
        accounts = pool.order()
        if not accounts:
            raise QuotaError("all Gemini accounts are cooling down after quota limits (see `squad accounts`)")
        errors = []
        for acc in accounts:
            try:
                r = self.call_account(acc, full, model)
                pool.mark_ok(acc["name"])
                return r
            except QuotaError as e:
                pool.mark_quota(acc["name"], self._cooldown_until(str(e)), str(e))
                errors.append(f"{acc['name']}: quota ({str(e)[:120]})")
            except AuthError as e:
                pool.mark_auth(acc["name"], str(e))
                errors.append(f"{acc['name']}: not signed in. Run `squad gemini login {acc['name']}`")
            except subprocess.TimeoutExpired:
                pool.mark_error(acc["name"], "timeout")
                errors.append(f"{acc['name']}: timed out")
            except WorkerError as e:
                if "not found on PATH" in str(e):
                    raise
                pool.mark_error(acc["name"], str(e))
                errors.append(f"{acc['name']}: {str(e)[:160]}")
        cls = QuotaError if all("quota" in e for e in errors) else WorkerError
        raise cls("Gemini failed on every account: " + "; ".join(errors))

    # ------------------------------------------------------------ login + status
    def login(self, name: str) -> int:
        """Open the CLI interactively for one account so you can choose 'Sign in with Google'."""
        acc = next((a for a in self.cfg["accounts"] if a.get("name") == name), None)
        if not acc:
            raise SystemExit(f"No Gemini account named {name!r}. Add it to squad.json -> workers.gemini.accounts")
        exe = self.exe()
        if not exe:
            raise SystemExit(f"'{self.cfg['command']}' not found. Install it: npm install -g @google/gemini-cli")
        print(f"Opening {self.cfg['command']} for account '{name}' (home: {self.pool.home(acc)}).\n"
              "Choose 'Sign in with Google', use the Google AI Pro account for this slot, then type /quit.")
        return subprocess.call([exe], env=self.account_env(acc))

    def status(self) -> dict:
        exe = self.exe()
        return {"name": self.name, "up": bool(exe), "command": self.cfg["command"], "model": self.cfg["model"],
                "rotation": self.cfg["rotation"], "accounts": self.pool.status(self.signed_in),
                "help": None if exe else "npm install -g @google/gemini-cli"}
