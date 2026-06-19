# Hermes Handoff — Agent-visible runtime context and graphify Codex lane

Date: 2026-06-19
Repo/path: `/opt/data/repos/hermes-project-backup` on `main`
Local/remote: VPS Hermes
Agent/model: Hermes Telegram session, gpt-5.5

## Why this session happened
- Jacob enabled BotFather Threads Settings for the Hermes Telegram bot and asked how `/topic` affects context windows.
- Jacob clarified that `/topic` does not meet the original “CLI workers ask me questions directly in Telegram” ask; that remains a future worker-backchannel feature.
- Jacob asked to build an agent-visible runtime line at the start of every gateway turn so Hermes can see current context pressure without relying on stale after-response footers.
- Jacob chose Codex workers as the graphify semantic extraction lane for now.

## What changed
- Added `gateway.runtime_footer.build_agent_runtime_context_line()` and `resolve_agent_runtime_context_config()`.
- Updated `gateway/run.py` to prepend a compact agent-visible line to each gateway user turn before the model call, for example: `[Runtime: context 25%, model gpt-5.5]`.
- Kept the existing 75% context handoff notice. When both are present, the runtime line appears first, then the handoff note.
- Added default config under `display.agent_runtime_context` in `hermes_cli/config.py`: enabled by default, fields `[context_pct, model]`.
- Added targeted tests in `tests/gateway/test_runtime_footer.py` for config resolution and rendered runtime line shape.
- Copied the two live gateway code files into `/opt/hermes/gateway/` and verified they match the repo copies. `hermes_cli/config.py` in `/opt/hermes` is not writable by Hermes, but the code helper defaults the new prefix to enabled even without a config entry.
- Graphify semantic chunks are set up procedurally through the existing `project-local/graphify` skill: Step B2 is Codex-based dispatch. A durable memory was also added: Jacob wants graphify semantic extraction to use Codex workers for now rather than default host/free-model extraction.

## Decisions and assumptions
- DECISION: `/topic` should be treated as one Telegram bot with many independent topic-bound Hermes sessions, each with its own history/context window. | reason: `gateway/run.py` gates non-root Telegram DM topics as lanes and binds each topic to a session ID. | needs Jacob confirmation: no
- DECISION: Do not pursue the direct CLI-to-Telegram worker conversation feature right now. | reason: useful, but bigger than `/topic` and only worth building if cron/auto-ingest gets blocked often. | needs Jacob confirmation: no
- DECISION: graphify semantic extraction should use Codex workers for now. | reason: Jacob distrusts OpenRouter free for graph quality. | needs Jacob confirmation: no
- ASSUMPTION: The live gateway must be restarted before copied `/opt/hermes/gateway/*.py` changes affect new Telegram turns. | risk if wrong: runtime prefix will not appear until restart/reload.

## Verification performed
- `uv run --with pytest --with pyyaml python -m pytest tests/gateway/test_runtime_footer.py -q -o 'addopts='` from `/opt/data/repos/hermes-project-backup` returned `32 passed in 0.28s`.
- `python3 -m py_compile /opt/hermes/gateway/runtime_footer.py /opt/hermes/gateway/run.py` succeeded after copying live gateway files.
- `cmp -s gateway/runtime_footer.py /opt/hermes/gateway/runtime_footer.py` returned `0` and `cmp -s gateway/run.py /opt/hermes/gateway/run.py` returned `0`, confirming live gateway files match repo copies.
- Confirmed the graphify skill at `/opt/data/skills/project-local/graphify/SKILL.md` already documents Codex Step B2 for semantic chunks.

## Open blockers / questions
- The running gateway process has not been restarted yet. Restarting from inside the current Telegram conversation may interrupt delivery, so do it at a clean boundary.
- Need to commit and push the repo changes after final review if Jacob wants this mirrored upstream.
- Future worker-backchannel feature remains unbuilt: worker/cron asks Jacob a question in Telegram, Jacob replies directly to that worker/task, worker resumes without Hermes mediating every message.

## Next recommended action
Commit and push the runtime-context changes, then restart the Hermes gateway at a clean boundary. After restart, send a small Telegram test message and confirm Hermes sees/uses the `[Runtime: context X%, model Y]` prefix behavior.

## Raw source / context to preserve
- `/opt/data/repos/hermes-project-backup/gateway/runtime_footer.py`
- `/opt/data/repos/hermes-project-backup/gateway/run.py`
- `/opt/data/repos/hermes-project-backup/hermes_cli/config.py`
- `/opt/data/repos/hermes-project-backup/tests/gateway/test_runtime_footer.py`
- `/opt/data/skills/project-local/graphify/SKILL.md`
- `/opt/data/skills/autonomous-ai-agents/hermes-agent/SKILL.md`
