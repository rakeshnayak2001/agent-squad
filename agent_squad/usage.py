"""Per-day usage ledger: calls, tokens, cost and seconds by worker (and by account)."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from .workers.base import Result


def record(path: Path, task: str, result: Result | None = None, worker: str = "", ok: bool = True,
           seconds: float = 0.0) -> None:
    try:
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        day = data.setdefault(dt.date.today().isoformat(), {})
        kind = result.worker if result else worker
        b = day.setdefault(kind, {"calls": 0, "failed": 0, "tokens_in": 0, "tokens_out": 0,
                                  "cost_usd": 0.0, "seconds": 0.0, "tasks": {}})
        b["calls"] += 1
        b["failed"] += 0 if ok else 1
        if result:
            b["tokens_in"] += int(result.tokens_in or 0)
            b["tokens_out"] += int(result.tokens_out or 0)
            b["cost_usd"] = round(b["cost_usd"] + float(result.cost_usd or 0), 4)
            seconds = result.seconds
            if result.account:
                acc = b.setdefault("accounts", {})
                acc[result.account] = acc.get(result.account, 0) + 1
        b["seconds"] = round(b["seconds"] + seconds, 1)
        b["tasks"][task] = b["tasks"].get(task, 0) + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(sorted(data.items())[-90:]), indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 - usage stats must never break a run
        pass


def summary(path: Path, days: int = 30) -> dict:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    keys = sorted(data)[-days:]
    tot: dict = {}
    for k in keys:
        for kind, b in data[k].items():
            t = tot.setdefault(kind, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
                                      "tasks": {}, "accounts": {}})
            for f in ("calls", "tokens_in", "tokens_out"):
                t[f] += b.get(f, 0)
            t["cost_usd"] = round(t["cost_usd"] + b.get("cost_usd", 0), 4)
            for group in ("tasks", "accounts"):
                for n, c in (b.get(group) or {}).items():
                    t[group][n] = t[group].get(n, 0) + c
    return {"days": {k: data[k] for k in keys}, "total": tot}
