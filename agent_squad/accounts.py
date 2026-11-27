"""A pool of signed-in accounts for one worker (used by Gemini; Codex can reuse it).

Each account has its own home folder, so two Google sign-ins never overwrite each other, and its own
line in <state_dir>/<worker>_accounts.json: calls today, last error, and a cooldown after a quota hit.

Rotation policies:
  failover     use accounts in the listed order; move on only when one is cooling down (default)
  round_robin  use the least recently used account that is not cooling down
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="microseconds")


class AccountPool:
    def __init__(self, worker: str, accounts: list[dict], accounts_dir: str | os.PathLike, state_dir: Path,
                 rotation: str = "failover"):
        self.worker = worker
        self.accounts = [a if isinstance(a, dict) else {"name": str(a)} for a in (accounts or [])]
        self.accounts_dir = Path(os.path.expanduser(str(accounts_dir)))
        self.state_file = Path(state_dir) / f"{worker}_accounts.json"
        self.rotation = rotation if rotation in ("failover", "round_robin") else "failover"

    # ------------------------------------------------------------ state
    def _load(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_file)

    def _update(self, name: str, **fields) -> None:
        data = self._load()
        rec = data.setdefault(name, {})
        rec.update(fields)
        self._save(data)

    # ------------------------------------------------------------ accounts
    def home(self, account: dict) -> Path:
        return Path(os.path.expanduser(account["home"])) if account.get("home") else self.accounts_dir / account["name"]

    def cooling_until(self, name: str) -> dt.datetime | None:
        until = self._load().get(name, {}).get("cooldown_until")
        if not until:
            return None
        t = dt.datetime.fromisoformat(until)
        return t if t > _now() else None

    def order(self) -> list[dict]:
        """Accounts to try, best first. Accounts on cooldown are left out."""
        ready = [a for a in self.accounts if not a.get("disabled") and not self.cooling_until(a["name"])]
        if self.rotation == "round_robin":
            st = self._load()
            ready.sort(key=lambda a: st.get(a["name"], {}).get("last_used", ""))
        return ready

    def mark_ok(self, name: str) -> None:
        data = self._load()
        rec = data.setdefault(name, {})
        today = dt.date.today().isoformat()
        calls = rec.get("calls", {})
        calls = {today: calls.get(today, 0) + 1} | {k: v for k, v in calls.items() if k != today}
        rec.update(last_used=_iso(_now()), last_ok=_iso(_now()), calls=dict(sorted(calls.items())[-14:]),
                   needs_login=False, cooldown_until=None)
        self._save(data)

    def mark_quota(self, name: str, until: dt.datetime, error: str) -> None:
        self._update(name, cooldown_until=_iso(until), last_error=error[:300], last_used=_iso(_now()))

    def mark_auth(self, name: str, error: str) -> None:
        self._update(name, needs_login=True, last_error=error[:300], last_used=_iso(_now()))

    def mark_error(self, name: str, error: str) -> None:
        self._update(name, last_error=error[:300], last_used=_iso(_now()))

    def reset(self, name: str | None = None) -> None:
        data = self._load()
        for n in ([name] if name else list(data)):
            if n in data:
                data[n]["cooldown_until"] = None
        self._save(data)

    def status(self, signed_in=None) -> list[dict]:
        st = self._load()
        today = dt.date.today().isoformat()
        out = []
        for a in self.accounts:
            rec = st.get(a["name"], {})
            cool = self.cooling_until(a["name"])
            out.append({"name": a["name"], "label": a.get("label", a["name"]), "home": str(self.home(a)),
                        "mode": "api_key_env" if a.get("api_key_env") else "default",
                        "signed_in": signed_in(a) if signed_in else None, "disabled": bool(a.get("disabled")),
                        "cooldown_until": _iso(cool) if cool else None, "calls_today": rec.get("calls", {}).get(today, 0),
                        "last_used": rec.get("last_used"), "last_error": rec.get("last_error"),
                        "needs_login": bool(rec.get("needs_login"))})
        return out
