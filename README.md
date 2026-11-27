# agent-squad

Claude Code is the **master**; other AIs are **workers** behind one small interface.
Use it on its own (CLI, Python, or an MCP server for Claude Code), or plug it into another app
such as [ai-youtube-shorts-automation](https://github.com/rakeshnayak2001/ai-youtube-shorts-automation).

| worker   | what it is                                   | auth                               |
|----------|----------------------------------------------|------------------------------------|
| `claude` | Claude Code headless (`claude -p`), master   | your Claude subscription           |
| `local`  | a local model through Ollama                 | none (free, private)               |
| `gemini` | Gemini API, default `gemini-3-pro-preview` | one Google AI Studio API key per account slot |
| `codex`  | planned: same interface (`codex exec`)       |                                    |

A route ("spec") is `worker` or `worker:model`: `local:qwen3:8b`, `gemini:gemini-3-pro-preview`, `claude:opus`.
If a worker fails, the `fallback` list in the config is tried in order.

## Install (Windows)
```
git clone https://github.com/rakeshnayak2001/agent-squad "C:\AI Vid\agent-squad"
"C:\AI Vid\agent-squad\setup.bat"
```
That installs the package (`pip install -e .[mcp]`), writes `~/.agent-squad/squad.json`, and asks for one or two
Gemini API keys (create them at https://aistudio.google.com/apikey).

## Use
```
squad status                          # which workers are ready, Gemini accounts
squad test gemini "Say hi in five words"
squad ask local --json < prompt.txt
squad gemini set-key acc1             # save an AI Studio API key for that slot (hidden input)
squad gemini models                   # models your key (or agy) can use
squad gemini use agy|api              # Pro plan via Antigravity CLI, or API keys
squad accounts [--reset]              # cooldowns after quota hits
squad usage                           # calls / tokens by worker and account
squad mcp                             # MCP server for Claude Code
```
(`python -m agent_squad ...` works too.)

```python
import agent_squad as squad
r = squad.ask("gemini", "Five festival ideas as a JSON list", json_mode=True, task="ideas")
print(r.text, r.worker, r.model, r.account, r.tokens_in, r.tokens_out)
```

### In Claude Code (master hands work to workers)
```
claude mcp add workers -- python -m agent_squad mcp
```
Tools: `list_workers`, `worker_generate`, `worker_generate_to_file`, `worker_batch`, each with
`worker="local" | "gemini" | "<worker>:<model>"`. Files are limited to `SQUAD_PROJECT_ROOT` (default: current folder).

## Gemini on your Google AI Pro plan (Antigravity CLI)
Gemini CLI no longer accepts Pro sign-ins, but Google's **Antigravity CLI (`agy`)** does. Run **`setup_pro.bat`**: it
installs `agy`, sets `workers.gemini.backend` to `"agy"` (`squad gemini use agy`), and opens the sign-in for your Pro
account. Then every Gemini call runs `agy -p <prompt> --output-format json` in an empty temp folder.
- One Pro account at a time (agy keeps its sign-in in the Windows credential store). Pick a model with
  `agy_model` (list: `squad gemini models`; empty = agy's default).
- Quota: the Pro plan's own limits (they refresh every few hours). On a quota error the slot cools down
  (`agy_cooldown_minutes`, default 60) and the router's fallback list takes over, e.g. the local model.
- Terms: Google has not clearly said whether scripted use of agy on a consumer plan is allowed (forum answers
  disagree). Keep it personal and low-volume; switch back with `squad gemini use api` if needed.

## Gemini keys and rotation
- **Backend `api` (default):** direct calls to the Gemini API with Google AI Studio keys. Each slot (`acc1`, `acc2`) has its
  own key, saved by `squad gemini set-key <slot>` in `~/.agent-squad/gemini/<slot>/api_key`, or read from an env var
  with `"api_key_env": "MY_GEMINI_KEY"`. Keys from two Google accounts give two separate quotas.
- `rotation: "failover"` (default): use slots in order; a slot that gets `429 RESOURCE_EXHAUSTED` cools down
  (30 min for per-minute limits, until about midnight US Pacific for daily ones) and the next is used.
  `"round_robin"` spreads calls across slots. If every slot is out, the router's fallback list is used.
- The API is billed by Google AI Studio: free-tier limits first, then pay-as-you-go if you turn billing on.
  A Google AI Pro subscription does not cover API calls.
- **Backend `cli`:** runs a Gemini-CLI-compatible command headless (`gemini -p ... --output-format json`).
  Since late 2026 Gemini CLI refuses Google AI Pro sign-ins ("migrate to the Antigravity suite"), so this backend
  is kept only for API-key or older setups.

## Config (`~/.agent-squad/squad.json`, or `$SQUAD_CONFIG`)
```json
{
  "state_dir": "~/.agent-squad/state",
  "default_worker": "local",
  "fallback": ["local", "claude"],
  "workers": {
    "claude": {"model": "", "allow_web_search": true},
    "local":  {"url": "http://localhost:11434", "model": "qwen3:8b", "temperature": 0.3},
    "gemini": {"backend": "api", "model": "gemini-3-pro-preview", "rotation": "failover",
               "accounts": [{"name": "acc1", "label": "Gemini key 1"}, {"name": "acc2", "label": "Gemini key 2"}]}
  }
}
```
A host app can pass its own dict instead (`squad.ask(..., config=...)`), or point the MCP server at it with
`SQUAD_SETTINGS_MODULE=<module>` exposing `squad_config()`.

## Add a worker (e.g. Codex)
1. `agent_squad/workers/codex.py`: subclass `Worker`, implement `generate()` returning a `Result`, and `status()`.
   Raise `QuotaError` / `AuthError` / `WorkerError` so rotation and fallback work.
2. Add it to `WORKERS` in `agent_squad/workers/__init__.py`.
3. Route tasks to `codex` or `codex:<model>`.

## Tests
`python -m pytest -q` (fake Gemini API server and fake Gemini CLI, no keys needed).
