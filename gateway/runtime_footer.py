"""Gateway runtime-metadata footer and agent-visible runtime status.

Renders a compact footer showing runtime state (model, context %, cwd) and
appends it to the FINAL message of an agent turn when enabled.  Off by default
to keep replies minimal.

Config (``~/.hermes/config.yaml``)::

    display:
      runtime_footer:
        enabled: true                       # off by default
        fields: [model, context_pct, cwd]   # order shown; drop any to hide

Per-platform overrides live under ``display.platforms.<platform>.runtime_footer``.
Users can toggle the global setting with ``/footer on|off`` from both the CLI
and any gateway platform.

The footer is appended to the final response text in ``gateway/run.py`` right
before returning the response to the adapter send path — so it only lands on
the final message a user sees, not on tool-progress updates or streaming
partials.  When streaming is on and the final text has already been delivered
piecemeal, the footer is sent as a separate trailing message via
``send_trailing_footer()``.

The agent-visible runtime context line is prepended to the next gateway user
turn before the model call. This lets the agent reason from the current
context percentage instead of relying on the user-visible footer, which the
agent cannot see until a later turn.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Optional

_DEFAULT_FIELDS: tuple[str, ...] = ("model", "context_pct", "cwd")
_DEFAULT_AGENT_CONTEXT_FIELDS: tuple[str, ...] = ("context_pct", "model")
_SEP = " · "
_DEFAULT_HANDOFF_THRESHOLD = 0.75


def _home_relative_cwd(cwd: str) -> str:
    """Return *cwd* with ``$HOME`` collapsed to ``~``.  Empty string if unset."""
    if not cwd:
        return ""
    try:
        home = os.path.expanduser("~")
        p = os.path.abspath(cwd)
        if home and (p == home or p.startswith(home + os.sep)):
            return "~" + p[len(home):]
        return p
    except Exception:
        return cwd


def _model_short(model: Optional[str]) -> str:
    """Drop ``vendor/`` prefix for readability (``openai/gpt-5.4`` → ``gpt-5.4``)."""
    if not model:
        return ""
    return model.rsplit("/", 1)[-1]


def resolve_footer_config(
    user_config: dict[str, Any] | None,
    platform_key: str | None = None,
) -> dict[str, Any]:
    """Resolve effective runtime-footer config for *platform_key*.

    Merge order (later wins):
        1. Built-in defaults (enabled=False)
        2. ``display.runtime_footer``
        3. ``display.platforms.<platform_key>.runtime_footer``
    """
    resolved = {"enabled": False, "fields": list(_DEFAULT_FIELDS)}
    cfg = (user_config or {}).get("display") or {}

    global_cfg = cfg.get("runtime_footer")
    if isinstance(global_cfg, dict):
        if "enabled" in global_cfg:
            resolved["enabled"] = bool(global_cfg.get("enabled"))
        if isinstance(global_cfg.get("fields"), list) and global_cfg["fields"]:
            resolved["fields"] = [str(f) for f in global_cfg["fields"]]

    if platform_key:
        platforms = cfg.get("platforms") or {}
        plat_cfg = platforms.get(platform_key)
        if isinstance(plat_cfg, dict):
            plat_footer = plat_cfg.get("runtime_footer")
            if isinstance(plat_footer, dict):
                if "enabled" in plat_footer:
                    resolved["enabled"] = bool(plat_footer.get("enabled"))
                if isinstance(plat_footer.get("fields"), list) and plat_footer["fields"]:
                    resolved["fields"] = [str(f) for f in plat_footer["fields"]]

    return resolved


def resolve_agent_runtime_context_config(
    user_config: dict[str, Any] | None,
    platform_key: str | None = None,
) -> dict[str, Any]:
    """Resolve config for the agent-visible per-turn runtime context line.

    Merge order mirrors ``resolve_footer_config``. Unlike the user-visible
    footer, this is enabled by default because it is a tiny system-note prefix
    that helps the agent decide whether to continue or hand off.

    Config::

        display:
          agent_runtime_context:
            enabled: true
            fields: [context_pct, model]
    """
    resolved = {"enabled": True, "fields": list(_DEFAULT_AGENT_CONTEXT_FIELDS)}
    cfg = (user_config or {}).get("display") or {}

    global_cfg = cfg.get("agent_runtime_context")
    if isinstance(global_cfg, dict):
        if "enabled" in global_cfg:
            resolved["enabled"] = bool(global_cfg.get("enabled"))
        if isinstance(global_cfg.get("fields"), list) and global_cfg["fields"]:
            resolved["fields"] = [str(f) for f in global_cfg["fields"]]

    if platform_key:
        platforms = cfg.get("platforms") or {}
        plat_cfg = platforms.get(platform_key)
        if isinstance(plat_cfg, dict):
            plat_context = plat_cfg.get("agent_runtime_context")
            if isinstance(plat_context, dict):
                if "enabled" in plat_context:
                    resolved["enabled"] = bool(plat_context.get("enabled"))
                if isinstance(plat_context.get("fields"), list) and plat_context["fields"]:
                    resolved["fields"] = [str(f) for f in plat_context["fields"]]

    return resolved


def format_runtime_footer(
    *,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
    fields: Iterable[str] = _DEFAULT_FIELDS,
) -> str:
    """Render the footer line, or return "" if no fields have data.

    Fields are skipped silently when their underlying data is missing — a
    partially-populated footer is better than a line with ``?%`` or empty slots.
    """
    parts: list[str] = []
    for field in fields:
        if field == "model":
            m = _model_short(model)
            if m:
                parts.append(m)
        elif field == "context_pct":
            if context_length and context_length > 0 and context_tokens >= 0:
                pct = max(0, min(100, round((context_tokens / context_length) * 100)))
                parts.append(f"{pct}%")
        elif field == "cwd":
            rel = _home_relative_cwd(cwd or os.environ.get("TERMINAL_CWD", ""))
            if rel:
                parts.append(rel)
        # Unknown field names are silently ignored.

    if not parts:
        return ""
    return _SEP.join(parts)


def build_footer_line(
    *,
    user_config: dict[str, Any] | None,
    platform_key: str | None,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
) -> str:
    """Top-level entry point used by gateway/run.py.

    Returns the footer text (empty string when disabled or no data).  Callers
    append this to the final response themselves, preserving a single blank
    line of separation.
    """
    cfg = resolve_footer_config(user_config, platform_key)
    if not cfg.get("enabled"):
        return ""
    return format_runtime_footer(
        model=model,
        context_tokens=context_tokens,
        context_length=context_length,
        cwd=cwd,
        fields=cfg.get("fields") or _DEFAULT_FIELDS,
    )


def build_agent_runtime_context_line(
    *,
    user_config: dict[str, Any] | None,
    platform_key: str | None,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
) -> str:
    """Return the compact agent-visible runtime context prefix line.

    Example: ``[Runtime: context 25%, model gpt-5.5]``. Empty when disabled
    or when no requested field has usable data.
    """
    cfg = resolve_agent_runtime_context_config(user_config, platform_key)
    if not cfg.get("enabled"):
        return ""

    parts: list[str] = []
    for field in cfg.get("fields") or _DEFAULT_AGENT_CONTEXT_FIELDS:
        if field == "context_pct":
            if context_length and context_length > 0 and context_tokens >= 0:
                pct = max(0, min(100, round((context_tokens / context_length) * 100)))
                parts.append(f"context {pct}%")
        elif field == "model":
            m = _model_short(model)
            if m:
                parts.append(f"model {m}")
        # Unknown fields are silently ignored for forward compatibility.

    if not parts:
        return ""
    return f"[Runtime: {', '.join(parts)}]"


def build_context_handoff_notice(
    *,
    user_config: dict[str, Any] | None,
    platform_key: str | None,
    context_tokens: int,
    context_length: Optional[int],
) -> str:
    """Return an agent-visible context-limit handoff notice, or ``""``.

    The gateway runtime footer is appended after the model has already produced
    its final response, so it is visible to the user but not to the agent.  This
    helper renders the equivalent context signal as a system-note prefix that
    the gateway can prepend to the next user turn when a session is getting
    full.

    Config::

        display:
          context_handoff_notice:
            enabled: true      # default
            threshold: 0.75    # fraction of context window
    """
    if not context_length or context_length <= 0 or context_tokens < 0:
        return ""

    cfg = (user_config or {}).get("display") or {}
    notice_cfg = cfg.get("context_handoff_notice")
    enabled = True
    threshold = _DEFAULT_HANDOFF_THRESHOLD
    if isinstance(notice_cfg, dict):
        if "enabled" in notice_cfg:
            enabled = bool(notice_cfg.get("enabled"))
        raw_threshold = notice_cfg.get("threshold")
        if raw_threshold is not None:
            try:
                threshold = float(raw_threshold)
            except (TypeError, ValueError):
                threshold = _DEFAULT_HANDOFF_THRESHOLD

    if not enabled:
        return ""
    threshold = max(0.01, min(0.99, threshold))
    pct = context_tokens / context_length
    if pct < threshold:
        return ""

    pct_int = max(0, min(100, round(pct * 100)))
    threshold_int = round(threshold * 100)
    return (
        f"[System note: This {platform_key or 'gateway'} session is around "
        f"{pct_int}% of the model context window, crossing the configured "
        f"{threshold_int}% handoff threshold. Before starting broad or "
        f"multi-step work, stop at a sane boundary, write or update the "
        f"repo's HERMES-HANDOFF.md with current state, verification, blockers, "
        f"and the next action, then tell Jacob to /reset or continue from "
        f"that handoff. If the user's request is small and safe, answer it "
        f"briefly and include the reset recommendation.]"
    )
