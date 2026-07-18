# Hermes Project Backup — Agent Instructions

## Immediate commits

Jacob wants an automatic **local** commit for every coherent, agent-authored change, including documentation. Do not ask whether to commit. Once the smallest relevant check passes:

1. Stage only the task files or hunks (`git add <paths>`); never use blanket staging.
2. Review `git diff --cached` and run `git diff --cached --check`.
3. Commit immediately with a concise message; do not wait until session end.

Do not push, merge, switch branches, amend, reset, rebase, or force-push merely to make the commit. Never stage secrets, credentials, private media, or pre-existing/unrelated work. If a file was already modified, stage only your hunk; leave it unstaged if it cannot be isolated safely.
