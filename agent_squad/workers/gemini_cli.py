"""Gemini worker with account rotation. Two backends:

  backend "api" (default): the Gemini API with Google AI Studio keys, one key per account.
      Save a key with `squad gemini set-key <account>` (stored in <accounts_dir>/<account>/api_key),
      or point the account at an env var with "api_key_env": "MY_KEY_VAR".
  backend "agy": Google's Antigravity CLI (`agy -p ... --output-format json`) signed in with ONE Google AI Pro
      account (agy keeps its sign-in in the OS keyring, so only one account at a time). Uses the Pro plan's
      quota (refreshes every few hours); on a quota error it cools down and the router falls back (e.g. to local).
      Sign in once with `squad gemini login pro`. Google has not clearly said whether scripted use of agy
      on a consumer plan is allowed: keep it personal and low-volume.
  backend "cli": a Gemini-CLI-compatible command run headless (gemini -p <prompt> --output-format json).
      Note: since late 2026 Gemini CLI refuses Google AI Pro sign-ins ("migrate to Antigravity"),
      so this backend only works with API keys or older setups.

Settings (squad.json -> workers.gemini):
  backend        "api" | "agy" | "cli"
  agy_command    "agy";  agy_model  "" (agy's default; see `squad gemini models`);  agy_cooldown_minutes  60
  command        "gemini" (cli backend)
  model          "gemini-3-pro-preview"
  rotation       "failover" | "round_robin"
  accounts       [{"name": "acc1", "label": "Key 1"}, {"name": "acc2"}]
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
from . import gemini_api
from .base import AuthError, QuotaError, Result, Worker, WorkerError

DEFAULTS = {
    "backend": "api", "command": "gemini", "model": "gemini-3-pro-preview", "rotation": "failover",
    "accounts": [{"name": "acc1", "label": "Gemini key 1"}, {"name": "acc2", "label": "Gemini key 2"}],
    "accounts_dir": "~/.agent-squad/gemini", "home_env": "GEMINI_CLI_HOME", "extra_args": [],
    "timeout": 600, "rate_limit_cooldown_minutes": 30, "quota_reset_utc_hour": 8,
    "agy_command": "agy", "agy_model": "", "agy_cooldown_minutes": 60,
    "agy_accounts": [{"name": "pro", "label": "Google AI Pro (Antigravity)"}],
}
AGY_INSTALL = "PowerShell: irm https://antigravity.google/cli/install.ps1 | iex"
PROMPT_FILE_OVER = 20000  # longer prompts are handed to agy as a file in its (empty) workspace
QUOTA_RE = re.compile(r"quota|RESOURCE_EXHAUSTED|\b429\b|rate.?limit|usage limit|too many requests|"
                      r"limit reached|out of credits", re.I)
DAILY_RE = re.compile(r"per.?day|daily|today|tomorrow", re.I)
AUTH_RE = re.compile(r"\bauth|log ?in|sign ?in|credential|unauthenticated|\b401\b|oauth|authentication required", re.I)
MAX_ARG_PROMPT = 6000  # longer prompts go through stdin (Windows command lines max out near 32k)


class GeminiCliWorker(Worker):
    name = "gemini"

    @property
    def cfg(self) -> dict:
        c = {**DEFAULTS, **self.settings}
        if c.get("backend") == "agy":
            c.update(command=c["agy_command"], model=c["agy_model"], accounts=c["agy_accounts"],
                     rate_limit_cooldown_minutes=c["agy_cooldown_minutes"])
        return c

    @property
    def pool(self) -> AccountPool:
        c = self.cfg
        return AccountPool(self.name, c["accounts"], c["accounts_dir"], Path(self.state_dir), c["rotation"])

    def exe(self) -> str | None:
        return shutil.which(self.cfg["command"])

    # ------------------------------------------------------------ per-account environment
    def account_env(self, account: dict) -> dict:
        env = dict(os.environ)
        if self.backend == "agy":  # agy keeps its sign-in in the OS keyring; no per-account home
            env.pop("GEMINI_API_KEY", None)
            return env
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

    @property
    def backend(self) -> str:
        b = self.settings.get("backend", DEFAULTS["backend"])
        return b if b in ("cli", "agy") else "api"

    def key_file(self, account: dict) -> Path:
        return self.pool.home(account) / "api_key"

    def api_key(self, account: dict) -> str:
        if account.get("api_key_env"):
            return os.environ.get(account["api_key_env"], "").strip()
        f = self.key_file(account)
        return f.read_text(encoding="utf-8").strip() if f.exists() else ""

    def set_key(self, name: str, key: str) -> Path:
        acc = self._account(name)
        f = self.key_file(acc)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(key.strip(), encoding="utf-8")
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
        return f

    def _account(self, name: str) -> dict:
        acc = next((a for a in self.cfg["accounts"] if a.get("name") == name), None)
        if not acc:
            raise SystemExit(f"No Gemini account named {name!r}. Add it to squad.json -> workers.gemini.accounts")
        return acc

    def signed_in(self, account: dict) -> bool | None:
        if self.backend == "agy":  # can't read the keyring: judge by the last call
            rec = self.pool._load().get(account["name"], {})
            return False if rec.get("needs_login") else (True if rec.get("last_ok") else None)
        if self.backend == "api" or account.get("api_key_env"):
            return bool(self.api_key(account))
        g = self.pool.home(account) / ".gemini"
        return any((g / f).exists() for f in ("oauth_creds.json", "google_accounts.json"))

    # ------------------------------------------------------------ one call
    def _run(self, account: dict, prompt: str, model: str) -> tuple[subprocess.CompletedProcess, float]:
        exe = self.exe()
        if not exe:
            how = AGY_INSTALL if self.backend == "agy" else "npm install -g @google/gemini-cli"
            raise WorkerError(f"'{self.cfg['command']}' not found on PATH. Install it: {how}")
        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix="squad-gemini-") as cwd:
            if self.backend == "agy":
                if len(prompt) > PROMPT_FILE_OVER:
                    Path(cwd, "prompt.txt").write_text(prompt, encoding="utf-8")
                    prompt = ("Read the file prompt.txt in the current folder and do exactly what it asks. "
                              "Reply with only the answer it asks for.")
                cmd = [exe, "-p", prompt, "--output-format", "json"] + (["--model", model] if model else [])
                stdin = ""
            else:
                # On Windows `gemini` is a .cmd shim: cmd.exe would re-parse quotes, & and % in the prompt,
                # so the prompt always goes through stdin there (and for long prompts everywhere).
                use_stdin = len(prompt) > MAX_ARG_PROMPT or exe.lower().endswith((".cmd", ".bat"))
                cmd = [exe, "-p", "Answer the request given on standard input." if use_stdin else prompt,
                       "--output-format", "json"] + (["-m", model] if model else [])
                stdin = prompt if use_stdin else ""
            cmd += list(self.cfg["extra_args"])
            res = subprocess.run(cmd, input=stdin, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", cwd=cwd, env=self.account_env(account),
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

    def call_account(self, account: dict, prompt: str, model: str, system: str = "", json_mode: bool = False,
                     temperature: float | None = None) -> Result:
        if self.backend == "api":
            key = self.api_key(account)
            if not key:
                raise AuthError(f"no API key saved for {account['name']}")
            r = gemini_api.generate(key, prompt, model=model, system=system, json_mode=json_mode,
                                    temperature=temperature, timeout=float(self.cfg["timeout"]))
            r.account = account["name"]
            return r
        res, secs = self._run(account, self.with_system(prompt, system, json_mode), model)
        out = self._parse(res.stdout)
        err = out.get("error") or (out.get("status") not in (None, "SUCCESS") and out.get("status"))
        msg = (json.dumps(err) if err else "") + " " + (res.stderr or "")
        if res.returncode != 0 or err or not out:
            text = msg.strip() or res.stdout[-500:] or f"exit code {res.returncode}"
            if QUOTA_RE.search(text):
                raise QuotaError(text[-500:])
            if AUTH_RE.search(text):
                raise AuthError(text[-500:])
            raise WorkerError(f"gemini exited {res.returncode}: {text[-500:]}")
        if "usage" in out:  # agy
            u = out.get("usage") or {}
            tin = int(u.get("input_tokens", 0) or 0)
            tout = int(u.get("output_tokens", 0) or 0) + int(u.get("thinking_tokens", 0) or 0)
            used = model or "agy-default"
        else:
            tin, tout, used = self._tokens(out.get("stats") or {})
        return Result(str(out.get("response", "")).strip(), self.name, used or model, account=account["name"],
                      tokens_in=tin, tokens_out=tout, seconds=secs, raw=out)

    def generate(self, prompt, *, system="", model=None, json_mode=False, temperature=None, task="") -> Result:
        model = model or self.cfg["model"]
        pool = self.pool
        accounts = pool.order()
        if not accounts:
            raise QuotaError("all Gemini accounts are cooling down after quota limits (see `squad accounts`)")
        errors = []
        for acc in accounts:
            try:
                r = self.call_account(acc, prompt, model, system, json_mode, temperature)
                pool.mark_ok(acc["name"])
                return r
            except QuotaError as e:
                pool.mark_quota(acc["name"], self._cooldown_until(str(e)), str(e))
                errors.append(f"{acc['name']}: quota ({str(e)[:120]})")
            except AuthError as e:
                pool.mark_auth(acc["name"], str(e))
                hint = "set-key" if self.backend == "api" else "login"
                if self.backend == "agy":
                    errors.append(f"{acc['name']}: not signed in to Antigravity. Run `squad gemini login {acc['name']}`")
                    continue
                errors.append(f"{acc['name']}: {str(e)[:120]}. Run `squad gemini {hint} {acc['name']}`")
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
        if self.backend == "api":
            return self.prompt_key(name)
        acc = self._account(name)
        exe = self.exe()
        if not exe:
            how = AGY_INSTALL if self.backend == "agy" else "npm install -g @google/gemini-cli"
            raise SystemExit(f"'{self.cfg['command']}' not found. Install it: {how}")
        if self.backend == "agy":
            print("Opening Antigravity CLI. If a browser opens, sign in with your Google AI Pro account.\n"
                  "When agy shows its prompt, you are signed in: type /quit (or press Ctrl+C) to close.")
            code = subprocess.call([exe], env=self.account_env(acc))
            self.pool.reset(name)
            self.pool._update(name, needs_login=False)
            print("Checking the sign-in with a short test call...")
            try:
                r = self.call_account(acc, "Reply with just the word OK.", self.cfg["model"])
                self.pool.mark_ok(name)
                print(f"Signed in and working ({r.text[:40]!r}). You can close this window.")
            except WorkerError as e:
                if isinstance(e, AuthError):
                    self.pool.mark_auth(name, str(e))
                print(f"The test call failed: {str(e)[:300]}")
            except subprocess.TimeoutExpired:
                print("The test call timed out.")
            try:
                input("Press Enter to close.")
            except EOFError:
                pass
            return code
        print(f"Opening {self.cfg['command']} for account '{name}' (home: {self.pool.home(acc)}).\n"
              "Choose 'Sign in with Google', use the Google AI Pro account for this slot, then type /quit.")
        return subprocess.call([exe], env=self.account_env(acc))

    def prompt_key(self, name: str) -> int:
        """Ask for an AI Studio API key in the terminal (hidden input), check it, and save it."""
        import getpass
        self._account(name)
        print(f"Gemini API key for '{name}'. Create one at https://aistudio.google.com/apikey\n"
              "Paste it here (input is hidden) and press Enter:")
        key = getpass.getpass("key: ").strip()
        if not key:
            print("No key entered; nothing saved.")
            return 1
        try:
            models = gemini_api.list_models(key)
        except WorkerError as e:
            print(f"That key did not work: {e}")
            return 1
        f = self.set_key(name, key)
        want = self.cfg["model"]
        print(f"Saved to {f}. The key can use {len(models)} models.")
        if want not in models:
            pro = [m for m in models if "pro" in m][:8]
            print(f"Note: the configured model {want!r} is not in its list. Pro models it can use: {', '.join(pro)}")
        input("Press Enter to close.")
        return 0

    def models(self, name: str | None = None) -> list[str]:
        if self.backend == "agy":
            exe = self.exe()
            if not exe:
                raise WorkerError(f"agy not found. Install it: {AGY_INSTALL}")
            res = subprocess.run([exe, "models"], capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=60)
            if res.returncode != 0:
                raise WorkerError((res.stderr or res.stdout).strip()[-300:] or "agy models failed")
            return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
        accs = [self._account(name)] if name else self.cfg["accounts"]
        for a in accs:
            key = self.api_key(a)
            if key:
                return gemini_api.list_models(key)
        raise WorkerError("no Gemini API key saved yet: run `squad gemini set-key acc1`")

    def status(self) -> dict:
        if self.backend == "api":
            up, help_ = True, None
        else:
            up = bool(self.exe())
            help_ = None if up else (AGY_INSTALL if self.backend == "agy" else "npm install -g @google/gemini-cli")
        return {"name": self.name, "up": up, "backend": self.backend, "command": self.cfg["command"],
                "model": self.cfg["model"], "rotation": self.cfg["rotation"],
                "accounts": self.pool.status(self.signed_in), "help": help_}
